const statusEl = document.getElementById("status");
const headerEl = document.getElementById("anime-header");
const listEl = document.getElementById("result-list");
const resLabel = document.getElementById("res-label");

renderNav();

// ---------- 返回链接：优先 history.back() 恢复搜索现场 ----------
// 搜索页把 keyword/page/type 同步在 URL 里，浏览器后退即可完整恢复；
// 直接打开本页（无站内 referrer）时退回搜索首页兜底。
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
  // titleZh 是后端从 AnimeGarden 资源标题反推的中文名，优先显示；没有则退回 AniList 原标题
  const title = anime.titleZh || t.romaji || t.native || t.english || "未知番剧";
  document.title = `${title} · 番剧资源`;
  document.getElementById("header-title").textContent = title;
  const sub = [anime.seasonYear, formatType(anime.format), anime.episodes ? `${anime.episodes}话` : ""]
    .filter(Boolean).join(" · ");
  document.getElementById("header-sub").textContent = sub;
  // 显示中文名的情况下，副标题行补上罗马音/日文/英文原名
  const nativeLine = [t.romaji, t.native, t.english].filter((x) => x && x !== title).join(" / ");
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

// ---------- 在线播放（选集 + 播放器） ----------
const videoEl = document.getElementById("player");
const playerEmpty = document.getElementById("player-empty");
const playerNow = document.getElementById("player-now");
const playerTip = document.getElementById("player-tip");
const epLabel = document.getElementById("ep-label");
const epGrid = document.getElementById("ep-grid");

let episodesData = []; // 选集按钮的 data-idx 指向这里
let activeBtn = null;
let hlsInstance = null; // m3u8 用的 Hls 实例，切集时先销毁旧的

function showPlayerError(text) {
  playerTip.textContent = text || "播放失败：该集链接可能已失效，试试其他集或用磁力下载";
  playerTip.style.display = "block";
}

function renderEpisodes(episodes) {
  episodesData = episodes || [];
  if (!episodesData.length) {
    playerEmpty.textContent = "暂无在线播放资源（ANi 以新番为主），可用磁力下载";
    return;
  }
  playerEmpty.style.display = "none";
  epLabel.style.display = "block";
  let sp = 0;
  epGrid.innerHTML = episodesData
    .map((ep, i) => {
      // 无集数的条目（剧场版/SP 等）编号 SP1/SP2，悬浮显示完整标题
      const label = ep.episode != null ? String(ep.episode) : `SP${++sp}`;
      return `<button type="button" class="ep-btn" data-idx="${i}" title="${escapeHtml(ep.title || "")}">${escapeHtml(label)}</button>`;
    })
    .join("");
  epGrid.querySelectorAll(".ep-btn").forEach((btn) =>
    btn.addEventListener("click", () => playEpisode(Number(btn.dataset.idx), btn)));
}

function playEpisode(idx, btn) {
  const ep = episodesData[idx];
  if (!ep || !ep.url) return;
  playerTip.style.display = "none";
  videoEl.style.display = "block";

  if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
  const isM3u8 = /\.m3u8($|\?)/i.test(ep.url);
  if (isM3u8 && window.Hls && Hls.isSupported()) {
    // 采集源的 m3u8：Chrome/Firefox 原生不支持 HLS，走 hls.js
    hlsInstance = new Hls();
    hlsInstance.loadSource(ep.url);
    hlsInstance.attachMedia(videoEl);
    hlsInstance.on(Hls.Events.ERROR, (_evt, data) => {
      if (data && data.fatal) showPlayerError();
    });
  } else {
    // mp4 直链（ANi）或 Safari 原生 m3u8；属性赋值不经 HTML 解析
    videoEl.src = ep.url;
  }
  videoEl.play().catch(() => {}); // 被自动播放策略拒绝时静默
  playerNow.textContent = ep.episode != null
    ? `正在播放：第 ${ep.episode} 集`
    : `正在播放：${ep.title || "SP"}`;
  if (activeBtn) activeBtn.classList.remove("active");
  btn.classList.add("active");
  activeBtn = btn;
}

// 链接失效/防盗链等导致播放失败时提示（切集时清除；m3u8 的致命错误由 Hls 事件走同一提示）
videoEl.addEventListener("error", () => {
  if (!videoEl.src && !hlsInstance) return;
  showPlayerError();
});

// ---------- 初始化 ----------
function init() {
  const qs = new URLSearchParams(location.search);
  const id = qs.get("id");
  const kw = qs.get("kw") || ""; // 搜索页透传的用户关键词，供视频源检索
  if (!id) { location.href = "index.html"; return; }

  statusEl.innerHTML = '<div class="loading-tip">正在加载番剧资源…</div>';

  // 两个数据源并行、互不阻塞：/videos 最坏要等上游 20 秒预算，
  // 不能拖住头部与磁力列表；任一失败只降级自己那一栏。
  API.getAnimeResources(id)
    .then((data) => {
      statusEl.innerHTML = "";
      renderHeader(data.anime || {});
      renderResources(data.resources || []);
    })
    .catch((err) => {
      statusEl.innerHTML = "";
      listEl.innerHTML = `<div class="empty-tip error">加载失败：${escapeHtml(err.message)}</div>`;
    });

  API.getAnimeVideos(id, kw)
    .then((data) => renderEpisodes((data && data.episodes) || []))
    .catch(() => renderEpisodes([]));
}
init();
