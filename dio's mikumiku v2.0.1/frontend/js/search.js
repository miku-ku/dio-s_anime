const form = document.getElementById("search-form");
const keywordInput = document.getElementById("keyword");
const typeSelect = document.getElementById("res-type");
const searchBtn = document.getElementById("search-btn");
const statusEl = document.getElementById("search-status");
const grid = document.getElementById("anime-grid");
const fallbackList = document.getElementById("fallback-list");
const pagination = document.getElementById("pagination");
const prevBtn = document.getElementById("prev-btn");
const nextBtn = document.getElementById("next-btn");
const pageInfo = document.getElementById("page-info");

const PAGE_SIZE = 20;

let currentPage = 1;
let lastKeyword = "";
let searchCtrl = null; // 每次搜索新建；快速翻页/连点时中止旧请求，防止响应乱序

renderNav();

function setStatus(html) { statusEl.innerHTML = html; }

// ---------- URL 状态同步：keyword/type/page 写入地址栏 ----------
// 用 replaceState 而非 pushState，避免翻页刷爆浏览器历史栈；
// 刷新页面、从详情页返回（history.back）时即可恢复搜索现场。
function syncUrl() {
  const qs = new URLSearchParams();
  if (lastKeyword) qs.set("keyword", lastKeyword);
  if (typeSelect.value) qs.set("type", typeSelect.value);
  if (currentPage > 1) qs.set("page", currentPage);
  const q = qs.toString();
  history.replaceState(null, "", q ? `?${q}` : location.pathname);
}

// ---------- 番剧卡片 ----------
function renderAnimes(animes) {
  grid.innerHTML = animes
    .map((a) => {
      const cover = (a.coverImage && (a.coverImage.large || a.coverImage.medium)) || "";
      // titleZh 是后端从 AnimeGarden 资源标题反推的中文名，优先显示；没有则退回 AniList 原标题
      const t = a.title || {};
      const title = a.titleZh || t.romaji || t.native || t.english || "未知番剧";
      const sub = [a.seasonYear, formatType(a.format), a.episodes ? `${a.episodes}话` : ""]
        .filter(Boolean).join(" · ");
      const coverHtml = cover
        ? `<img class="anime-cover" src="${escapeHtml(cover)}" alt="${escapeHtml(title)}" loading="lazy" data-initial="${escapeHtml(title.charAt(0))}" />`
        : `<div class="cover-fallback">${escapeHtml(title.charAt(0))}</div>`;
      // 透传用户搜索词（kw）：详情页的视频源检索用它命中率最高
      const kwQs = lastKeyword ? `&kw=${encodeURIComponent(lastKeyword)}` : "";
      return `
        <a class="anime-card card" href="anime.html?id=${a.id}${kwQs}">
          <div class="cover-wrap">${coverHtml}</div>
          <div class="anime-info">
            <div class="anime-title">${escapeHtml(title)}</div>
            <div class="anime-sub">${escapeHtml(sub)}</div>
          </div>
        </a>`;
    })
    .join("");

  // 图片加载失败时换成首字占位
  grid.querySelectorAll("img.anime-cover").forEach((img) => {
    img.addEventListener("error", () => {
      img.parentElement.innerHTML = `<div class="cover-fallback">${img.dataset.initial || "?"}</div>`;
    });
  });
}

// ---------- 分页 ----------
function renderPagination(pi) {
  const hasPrev = currentPage > 1;
  const hasNext = !!(pi && pi.hasNextPage);
  prevBtn.disabled = !hasPrev;
  nextBtn.disabled = !hasNext;
  // AniList 的 pageInfo.total 是总条数，每页固定 PAGE_SIZE 条
  const total = pi && typeof pi.total === "number" ? pi.total : 0;
  pageInfo.textContent = total
    ? `共 ${total} 部 · 第 ${currentPage}/${Math.max(1, Math.ceil(total / PAGE_SIZE))} 页`
    : `第 ${currentPage} 页`;
  pagination.style.display = hasPrev || hasNext ? "flex" : "none";
}

// 资源直搜模式的分页：AnimeGarden 响应里没有 hasNextPage，
// 用「满页则可能有下一页」的近似判断
function renderResourcePagination(count) {
  prevBtn.disabled = currentPage <= 1;
  nextBtn.disabled = count < PAGE_SIZE;
  pageInfo.textContent = `第 ${currentPage} 页`;
  pagination.style.display = "flex";
}

// ---------- 资源卡片列表渲染（兜底 / 类型筛选共用） ----------
function showResources(resources, emptyTip) {
  fallbackList.style.display = "flex";
  if (!resources.length) {
    fallbackList.innerHTML = `<div class="empty-tip">${escapeHtml(emptyTip)}</div>`;
    return;
  }
  fallbackList.innerHTML = resources.map(resourceCardHtml).join("");
  bindCopyButtons(fallbackList);
}

// ---------- 搜索 ----------
// 类型筛选模式：type 是资源级属性，AniList 番剧卡片没有这个维度，
// 所以选了类型就切换为资源直搜（后端代理 AnimeGarden，支持 type 参数）
async function doResourceSearch(page, signal) {
  setStatus('<div class="loading-tip">正在按类型搜索资源…</div>');
  fallbackList.style.display = "flex";
  fallbackList.innerHTML = '<div class="empty-tip">正在搜索资源…</div>';
  try {
    const data = await API.searchAnime({
      keyword: lastKeyword, page, pageSize: PAGE_SIZE, type: typeSelect.value, signal,
    });
    const resources = data.resources || [];
    setStatus("");
    showResources(resources, "没有找到相关资源，换个关键词或类型试试～");
    renderResourcePagination(resources.length);
  } catch (err) {
    if (err.name === "AbortError") return;
    fallbackList.innerHTML = `<div class="empty-tip error">搜索失败：${escapeHtml(err.message)}</div>`;
  }
}

// AniList 无结果时的兜底：资源站直搜
async function renderFallback(keyword, signal) {
  fallbackList.style.display = "flex";
  fallbackList.innerHTML = '<div class="empty-tip">正在为你搜索资源站…</div>';
  try {
    const data = await API.searchAnime({ keyword, page: 1, pageSize: PAGE_SIZE, signal });
    showResources(data.resources || [], "资源站也没有找到相关结果，换个关键词试试～");
  } catch (err) {
    if (err.name === "AbortError") return;
    fallbackList.innerHTML = `<div class="empty-tip error">兜底搜索失败：${escapeHtml(err.message)}</div>`;
  }
}

async function doSearch(page = 1) {
  // 中止上一次未完成的请求，快速连点时只有最后一次生效
  if (searchCtrl) searchCtrl.abort();
  searchCtrl = new AbortController();
  const signal = searchCtrl.signal;

  currentPage = page;
  lastKeyword = keywordInput.value.trim();
  syncUrl();

  searchBtn.disabled = true;
  searchBtn.textContent = "搜索中…";
  prevBtn.disabled = nextBtn.disabled = true;
  grid.innerHTML = "";
  fallbackList.style.display = "none";
  fallbackList.innerHTML = "";
  pagination.style.display = "none";

  try {
    if (typeSelect.value) {
      await doResourceSearch(page, signal);
      return;
    }
    setStatus('<div class="loading-tip">正在搜索番剧，请稍候…</div>');
    const data = await API.searchAnimes(lastKeyword, page, { signal });
    const animes = data.animes || [];
    if (animes.length) {
      setStatus("");
      renderAnimes(animes);
      renderPagination(data.pageInfo);
    } else if (lastKeyword) {
      // AniList 没匹配到（多为纯中文名）→ 提示一次，下方直接渲染资源站结果
      setStatus('<div class="empty-tip">番剧库未收录该关键词，以下为资源站直搜结果</div>');
      await renderFallback(lastKeyword, signal);
    } else {
      setStatus('<div class="empty-tip">没有找到内容</div>');
    }
  } catch (err) {
    if (err.name === "AbortError") return; // 已被更新的请求取代，不碰界面
    setStatus(`<div class="empty-tip error">出错了：${escapeHtml(err.message)}</div>`);
  } finally {
    if (!signal.aborted) { // 只有最后一次请求才恢复按钮
      searchBtn.disabled = false;
      searchBtn.textContent = "搜索";
    }
  }
}

form.addEventListener("submit", (e) => { e.preventDefault(); doSearch(1); });
typeSelect.addEventListener("change", () => doSearch(1));
prevBtn.addEventListener("click", () => doSearch(currentPage - 1));
nextBtn.addEventListener("click", () => doSearch(currentPage + 1));

// 首次进入：从 URL 恢复搜索状态（刷新/详情页返回时保留现场），没有则加载热门番剧
(function initFromUrl() {
  const qs = new URLSearchParams(location.search);
  const kw = qs.get("keyword") || "";
  const type = qs.get("type") || "";
  const page = Math.max(1, parseInt(qs.get("page"), 10) || 1);
  keywordInput.value = kw;
  if ([...typeSelect.options].some((o) => o.value === type)) typeSelect.value = type;
  doSearch(page);
})();
