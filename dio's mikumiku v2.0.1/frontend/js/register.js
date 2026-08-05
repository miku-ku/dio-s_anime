const form = document.getElementById("register-form");
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
  const confirm = form.confirm.value;

  if (password !== confirm) {
    showMessage("两次输入的密码不一致", "error");
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = "注册中…";
  const remember = form.remember.checked;
  try {
    const data = await API.register(username, password, remember);
    API.setToken(data.token, remember); // 注册成功直接登录；勾选“记住我”时令牌 30 天有效
    showMessage("注册成功，正在跳转…", "success");
    setTimeout(() => (location.href = "index.html"), 600);
  } catch (err) {
    showMessage(err.message, "error");
    submitBtn.disabled = false;
    submitBtn.textContent = "注册";
  }
});
