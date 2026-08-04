const statusEl = document.getElementById("status");
const headerEl = document.getElementById("anime-header");
const listEl = document.getElementById("result-list");
const resLabel = document.getElementById("res-label");

renderNav();

// ---------- 返回链接：优先 history.back() 恢复搜索现场 ----------
// 搜索页把 keyword/page/type 同步在 URL 里，浏览器后退即可完整恢复；
// 直接打开本页（无站内 referrer）时退回 search.html 兜底。
document.querySelector(".back-link").addEventListener("click", (e) => {
  const ref = document.referrer;
  const fromSameOrigin = ref && new URL(ref).origin === location.origin;
  if (fromSameOrigin && history.length > 1) {
    e.preventDefault();
    history.back();
  }
});

// ---------- 渲染番剧头部 ----------
function renderHeader(anime) {
  const t = anime.title || {};
  const title = t.romaji || t.native || t.english || "未知番剧";
  document.title = `${title} · 番剧资源`;
  document.getElementById("header-title").textContent = title;
  const sub = [anime.seasonYear, formatType(anime.format), anime.episodes ? `${anime.episodes}话` : ""]
    .filter(Boolean).join(" · ");
  document.getElementById("header-sub").textContent = sub;
  const nativeLine = [t.native, t.english].filter((x) => x && x !== title).join(" / ");
  document.getElementById("header-native").textContent = nativeLine;

  const cover = (anime.coverImage && (anime.coverImage.large || anime.coverImage.medium)) || "";
  const coverWrap = document.getElementById("header-cover");
  if (cover) {
    coverWrap.innerHTML = `<img class="anime-cover" src="${escapeHtml(cover)}" alt="${escapeHtml(title)}" data-initial="${escapeHtml(title.charAt(0))}" />`;
    coverWrap.querySelector("img").addEventListener("error", function () {
      coverWrap.innerHTML = `<div class="cover-fallback">${this.dataset.initial || "?"}</div>`;
    });
  } else {
    coverWrap.innerHTML = `<div class="cover-fallback">${escapeHtml(title.charAt(0))}</div>`;
  }
  headerEl.style.display = "flex";
}

// ---------- 渲染资源列表 ----------
function renderResources(resources) {
  if (!resources.length) {
    resLabel.style.display = "none";
    listEl.innerHTML = '<div class="empty-tip">资源站暂未收录这部番剧的磁力资源，过段时间再来看看吧～</div>';
    return;
  }
  resLabel.style.display = "block";
  resLabel.textContent = `磁力资源（${resources.length}）`;
  listEl.innerHTML = resources.map(resourceCardHtml).join("");
  bindCopyButtons(listEl);
}

// ---------- 初始化 ----------
async function init() {
  const id = new URLSearchParams(location.search).get("id");
  if (!id) { location.href = "search.html"; return; }

  statusEl.innerHTML = '<div class="loading-tip">正在加载番剧资源…</div>';
  try {
    const data = await API.getAnimeResources(id);
    statusEl.innerHTML = "";
    renderHeader(data.anime);
    renderResources(data.resources || []);
  } catch (err) {
    statusEl.innerHTML = `<div class="empty-tip error">加载失败：${escapeHtml(err.message)}</div>`;
  }
}
init();
