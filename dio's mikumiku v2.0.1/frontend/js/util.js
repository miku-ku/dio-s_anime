// 公共工具函数：所有页面共享。
// 引入顺序要求：api.js → util.js → 页面 js（renderNav 依赖 API，页面 js 依赖这里）。

// ---------- 导航栏（右上角头像菜单，按登录态渲染） ----------
// 站点入口是搜索首页（index.html）；登录/注册/个人主页都收进头像下拉菜单。
let _navDocClickBound = false;

function renderNav() {
  const navLinks = document.getElementById("nav-links");
  if (!navLinks) return;
  navLinks.innerHTML = `
    <div class="nav-user">
      <button type="button" class="nav-avatar-btn" id="nav-avatar-btn" aria-haspopup="menu" title="用户菜单">
        <img class="nav-avatar-img" id="nav-avatar-img" alt="头像" style="display:none" />
        <span class="nav-avatar-fallback" id="nav-avatar-fallback">👤</span>
      </button>
      <div class="nav-menu" id="nav-menu" role="menu"></div>
    </div>`;

  const menu = document.getElementById("nav-menu");
  document.getElementById("nav-avatar-btn").addEventListener("click", (e) => {
    e.stopPropagation(); // 别让冒泡到 document 的监听把菜单立刻关掉
    menu.classList.toggle("show");
  });
  // 点页面任意空白处收起菜单；renderNav 可能因 401 重入，document 监听只绑一次
  if (!_navDocClickBound) {
    document.addEventListener("click", () => {
      const m = document.getElementById("nav-menu");
      if (m) m.classList.remove("show");
    });
    _navDocClickBound = true;
  }

  if (!API.getToken()) {
    menu.innerHTML = '<a href="login.html">登录</a><a href="register.html">注册</a>';
    return;
  }

  menu.innerHTML = '<a href="profile.html">个人主页</a><a href="#" id="nav-logout">退出</a>';
  document.getElementById("nav-logout").addEventListener("click", (e) => {
    e.preventDefault();
    API.clearToken();
    location.href = "login.html";
  });

  // 拉当前用户信息渲染头像；401 → 清 token 退回游客菜单；
  // 其他错误（断网）保留占位图标——断网不等于登出，与个人主页的处理一致
  API.me()
    .then((user) => {
      const img = document.getElementById("nav-avatar-img");
      const fb = document.getElementById("nav-avatar-fallback");
      if (!img) return; // 401 重渲染等场景下节点可能已被替换
      if (user.avatar) {
        img.src = user.avatar;
        img.style.display = "block";
        fb.style.display = "none";
      } else if (user.username) {
        fb.textContent = user.username.charAt(0).toUpperCase();
      }
    })
    .catch((err) => {
      if (err && err.status === 401) {
        API.clearToken();
        renderNav();
      }
    });
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
