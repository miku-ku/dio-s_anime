// 公共工具函数：所有页面共享。
// 引入顺序要求：api.js → util.js → 页面 js（renderNav 依赖 API，页面 js 依赖这里）。

// ---------- 导航栏（按登录态渲染） ----------
function renderNav() {
  const navLinks = document.getElementById("nav-links");
  if (!navLinks) return;
  const logged = !!API.getToken();
  navLinks.innerHTML = logged
    ? '<a href="search.html">动漫搜索</a><a href="index.html">个人主页</a><a href="#" id="nav-logout">退出</a>'
    : '<a href="search.html">动漫搜索</a><a href="login.html">登录</a><a href="register.html">注册</a>';
  if (logged) {
    document.getElementById("nav-logout").addEventListener("click", (e) => {
      e.preventDefault();
      API.clearToken();
      location.href = "login.html";
    });
  }
}

// ---------- HTML 转义（防 XSS） ----------
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ---------- 格式化 ----------
const FORMAT_MAP = { TV: "TV", MOVIE: "剧场版", OVA: "OVA", ONA: "ONA", SPECIAL: "SP", MUSIC: "MV" };
function formatType(f) { return FORMAT_MAP[f] || f || ""; }

function formatSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = Number(bytes);
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatDate(ts) {
  if (!ts) return "";
  const d = new Date(typeof ts === "number" ? ts : Date.parse(ts));
  if (isNaN(d)) return "";
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// ---------- Toast 轻提示 ----------
let _toastTimer;
function showToast(text, type = "success") {
  const toast = document.getElementById("toast");
  if (!toast) return; // 没有 toast 容器的页面静默跳过
  toast.textContent = text;
  toast.className = `toast show ${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => (toast.className = "toast"), 2200);
}

// ---------- 复制磁力链 ----------
async function copyMagnet(text, btn) {
  if (!text) { showToast("该资源没有磁力链", "error"); return; }
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; }
  catch {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    document.body.removeChild(ta);
  }
  if (ok) {
    showToast("磁力链已复制，去下载工具里粘贴吧");
    if (btn) { const o = btn.textContent; btn.textContent = "已复制 ✓"; setTimeout(() => (btn.textContent = o), 1400); }
  } else showToast("复制失败，请手动复制", "error");
}

// ---------- 资源卡片（搜索页兜底/类型筛选 与 番剧详情页共用） ----------
function resourceCardHtml(r) {
  return `
    <div class="res-card card">
      <div class="res-top">
        <span class="res-type">${escapeHtml(r.type || "资源")}</span>
        <span class="res-date">${formatDate(r.createdAt)}</span>
      </div>
      <div class="res-title">${escapeHtml(r.title)}</div>
      <div class="res-meta">
        ${r.publisher?.name ? `<span>字幕组：${escapeHtml(r.publisher.name)}</span>` : ""}
        <span>${formatSize(r.size)}</span>
      </div>
      <div class="res-actions">
        <button class="btn copy-btn" data-magnet="${escapeHtml(r.magnet || "")}">复制磁力链</button>
        ${r.href ? `<a class="btn btn-secondary" href="${escapeHtml(r.href)}" target="_blank" rel="noopener">源站</a>` : ""}
      </div>
    </div>`;
}

function bindCopyButtons(container) {
  container.querySelectorAll(".copy-btn").forEach((btn) =>
    btn.addEventListener("click", () => copyMagnet(btn.dataset.magnet, btn)));
}
