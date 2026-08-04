const form = document.getElementById("login-form");
const messageEl = document.getElementById("message");
const submitBtn = document.getElementById("submit-btn");

renderNav(); // 已登录用户访问本页时导航会显示“退出”而非写死的登录/注册

function showMessage(text, type) {
  messageEl.textContent = text;
  messageEl.className = `message ${type}`;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const username = form.username.value.trim();
  const password = form.password.value;
  const remember = form.remember.checked;

  submitBtn.disabled = true;
  submitBtn.textContent = "登录中…";
  try {
    const data = await API.login(username, password, remember);
    API.setToken(data.token, remember);
    showMessage("登录成功，正在跳转…", "success");
    setTimeout(() => (location.href = "index.html"), 600);
  } catch (err) {
    showMessage(err.message, "error");
    submitBtn.disabled = false;
    submitBtn.textContent = "登录";
  }
});
