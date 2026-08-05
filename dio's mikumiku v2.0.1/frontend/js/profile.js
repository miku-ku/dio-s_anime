const pageLoading = document.getElementById("page-loading");
const guestView = document.getElementById("guest-view");
const errorView = document.getElementById("error-view");
const userView = document.getElementById("user-view");
const sideMessage = document.getElementById("side-message");
const messageEl = document.getElementById("message");

let currentUser = null;

// ---------- 通用 ----------
function showMessage(el, text, type) {
  el.textContent = text;
  el.className = `message ${type}`;
}

function logout() {
  API.clearToken();
  location.href = "login.html";
}

function showView(view) {
  pageLoading.style.display = "none";
  guestView.style.display = "none";
  errorView.style.display = "none";
  userView.style.display = "none";
  if (view) view.style.display = view === userView ? "grid" : "block";
}

function renderAvatar(user) {
  const img = document.getElementById("avatar-img");
  const letter = document.getElementById("avatar");
  if (user.avatar) {
    img.src = user.avatar + "?t=" + Date.now(); // 时间戳避免浏览器缓存旧头像
    img.style.display = "block";
    letter.style.display = "none";
  } else {
    letter.textContent = user.username.charAt(0).toUpperCase();
    img.style.display = "none";
    letter.style.display = "flex";
  }
}

function renderBio(user) {
  const bioEl = document.getElementById("bio-text");
  if (user.bio && user.bio.trim()) {
    bioEl.textContent = user.bio;
    bioEl.classList.remove("empty");
  } else {
    bioEl.textContent = "这个人很懒，什么都没写";
    bioEl.classList.add("empty");
  }
}

function renderProfile(user) {
  renderAvatar(user);
  renderBio(user);
  document.getElementById("username").textContent = user.username;
  document.getElementById("meta").textContent = user.created_at
    ? `注册于 ${user.created_at}`
    : "";
}

// ---------- 初始化 ----------
async function init() {
  if (!API.getToken()) {
    showView(guestView);
    renderNav();
    return;
  }
  try {
    currentUser = await API.me(); // 用 token 向后端确认登录状态
    renderProfile(currentUser);
    showView(userView);
    renderNav();
  } catch (err) {
    if (err.status === 401) {
      // token 无效/过期/被吊销 → 清除并显示游客视图
      API.clearToken();
      showView(guestView);
      renderNav();
    } else {
      // 网络异常等 → 保留 token（断网不等于登出），提示重试
      showView(errorView);
      renderNav();
    }
  }
}
document.getElementById("retry-btn").addEventListener("click", () => location.reload());
init();

// ---------- 更换头像 ----------
const chooseBtn = document.getElementById("choose-avatar-btn");
const avatarInput = document.getElementById("avatar-input");
const AVATAR_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];
const AVATAR_MAX_SIZE = 2 * 1024 * 1024;

chooseBtn.addEventListener("click", () => avatarInput.click());

avatarInput.addEventListener("change", async () => {
  const file = avatarInput.files[0];
  if (!file) return;
  // 客户端预检，省一次往返（后端仍会按魔数与大小做权威校验）
  if (!AVATAR_TYPES.includes(file.type) || file.size > AVATAR_MAX_SIZE) {
    showMessage(sideMessage, "请选择 JPG/PNG/GIF/WebP 图片且不超过 2MB", "error");
    avatarInput.value = "";
    return;
  }
  chooseBtn.disabled = true;
  chooseBtn.textContent = "上传中…";
  try {
    await API.uploadAvatar(file);
    currentUser = await API.me();
    renderAvatar(currentUser);
    showMessage(sideMessage, "头像更新成功", "success");
  } catch (err) {
    showMessage(sideMessage, err.message, "error");
  } finally {
    chooseBtn.disabled = false;
    chooseBtn.textContent = "选择新头像";
    avatarInput.value = "";
  }
});

// ---------- 修改密码 ----------
const pwForm = document.getElementById("password-form");

pwForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const oldPw = document.getElementById("old-password").value;
  const newPw = document.getElementById("new-password").value;
  const confirmPw = document.getElementById("confirm-password").value;

  if (newPw !== confirmPw) {
    showMessage(sideMessage, "两次输入的新密码不一致", "error");
    return;
  }

  const btn = document.getElementById("password-btn");
  btn.disabled = true;
  btn.textContent = "提交中…";
  try {
    await API.changePassword(oldPw, newPw);
    showMessage(sideMessage, "密码修改成功，请重新登录", "success");
    // 改密后后端 token_version +1，当前令牌立即失效，必须重新登录
    API.clearToken();
    setTimeout(() => (location.href = "login.html"), 1000);
  } catch (err) {
    showMessage(sideMessage, err.message, "error");
    btn.disabled = false;
    btn.textContent = "修改密码";
  }
});

// ---------- 简介弹窗 ----------
const overlay = document.getElementById("modal-overlay");
const bioInput = document.getElementById("bio-input");
const charCount = document.getElementById("char-count");
const editBtn = document.getElementById("edit-bio-btn");
const saveBtn = document.getElementById("bio-save");

function updateCount() {
  charCount.textContent = `${bioInput.value.length}/200`;
}

function openModal() {
  bioInput.value = currentUser.bio || "";
  updateCount();
  overlay.style.display = "flex";
  bioInput.focus();
}

function closeModal() {
  overlay.style.display = "none";
}

editBtn.addEventListener("click", openModal);
document.getElementById("bio-cancel").addEventListener("click", closeModal);
document.getElementById("modal-close").addEventListener("click", closeModal);
overlay.addEventListener("click", (e) => {
  if (e.target === overlay) closeModal(); // 点击遮罩空白处关闭
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && overlay.style.display === "flex") closeModal();
});
bioInput.addEventListener("input", updateCount);

saveBtn.addEventListener("click", async () => {
  saveBtn.disabled = true;
  saveBtn.textContent = "保存中…";
  try {
    currentUser = await API.updateBio(bioInput.value);
    renderBio(currentUser);
    closeModal();
    showMessage(messageEl, "简介已更新", "success");
  } catch (err) {
    showMessage(messageEl, err.message, "error");
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = "保存";
  }
});

// ---------- 退出登录 ----------
document.getElementById("logout-btn").addEventListener("click", logout);
