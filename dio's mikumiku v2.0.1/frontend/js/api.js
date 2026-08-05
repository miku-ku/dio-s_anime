// 统一的后端请求封装：自动携带 JWT、统一处理错误。
// 前端由 FastAPI 托管，二者同源，所以直接用相对路径 /api/... 即可，无需处理跨域。

const API = (() => {
  // “记住我” → localStorage（关浏览器后仍保留）；否则 → sessionStorage（关浏览器即失效）
  const TOKEN_KEY = "token";

  function getToken() {
    return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY);
  }

  function setToken(token, remember = false) {
    if (remember) {
      localStorage.setItem(TOKEN_KEY, token);
      sessionStorage.removeItem(TOKEN_KEY);
    } else {
      sessionStorage.setItem(TOKEN_KEY, token);
      localStorage.removeItem(TOKEN_KEY);
    }
  }

  function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
  }

  // FastAPI 422 校验错误是英文的，这里按 d.type 做常见情况的中文映射
  const FIELD_LABELS = {
    username: "用户名",
    password: "密码",
    old_password: "原密码",
    new_password: "新密码",
    bio: "简介",
  };
  function zhValidationError(d) {
    const field = Array.isArray(d?.loc) ? d.loc[d.loc.length - 1] : "";
    const label = FIELD_LABELS[field] || field;
    let msg;
    switch (d?.type) {
      case "missing": msg = "不能为空"; break;
      case "string_too_short": msg = `太短（至少 ${d.ctx?.min_length ?? "?"} 位）`; break;
      case "string_too_long": msg = `太长（最多 ${d.ctx?.max_length ?? "?"} 位）`; break;
      case "string_pattern_mismatch": msg = "格式不符合要求"; break;
      default: msg = d?.msg || "格式不符合要求";
    }
    return label ? `${label}${msg}` : msg;
  }

  // 核心请求方法；isFormData 用于文件上传（不手动设置 Content-Type）；
  // signal 可传 AbortController.signal 用于丢弃过期请求
  async function request(path, { method = "GET", body, isFormData = false, signal } = {}) {
    const headers = {};
    if (!isFormData) headers["Content-Type"] = "application/json";
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;

    let res;
    try {
      res = await fetch(path, {
        method,
        headers,
        body: isFormData ? body : body ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (err) {
      if (err && err.name === "AbortError") throw err; // 主动中止的请求由调用方静默忽略
      const netErr = new Error("网络异常，无法连接服务器，请检查网络");
      netErr.status = 0; // status=0 表示网络层错误（区别于 401 等 HTTP 错误）
      throw netErr;
    }

    let data = null;
    try {
      data = await res.json();
    } catch {
      /* 某些响应没有 JSON 体 */
    }

    if (!res.ok) {
      // FastAPI 校验失败时 detail 是数组，普通错误是字符串，这里都转成可读文本
      const detail = data?.detail;
      const message = Array.isArray(detail)
        ? detail.map(zhValidationError).join("；")
        : detail || `请求失败（${res.status}）`;
      const err = new Error(message);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  return {
    getToken,
    setToken,
    clearToken,
    register: (username, password, remember = false) =>
      request("/api/register", {
        method: "POST",
        body: { username, password, remember },
      }),
    login: (username, password, remember = false) =>
      request("/api/login", { method: "POST", body: { username, password, remember } }),
    me: () => request("/api/me"),
    changePassword: (oldPassword, newPassword) =>
      request("/api/change-password", {
        method: "POST",
        body: { old_password: oldPassword, new_password: newPassword },
      }),
    updateBio: (bio) =>
      request("/api/bio", { method: "POST", body: { bio } }),
    searchAnime: ({ keyword = "", page = 1, pageSize = 20, type = "", signal } = {}) => {
      const qs = new URLSearchParams({ page, pageSize });
      if (keyword) qs.set("keyword", keyword);
      if (type) qs.set("type", type);
      return request(`/api/anime/search?${qs.toString()}`, { signal });
    },
    // 番剧搜索（AniList，带封面）；keyword 为空时返回热门番
    searchAnimes: (keyword = "", page = 1, { signal } = {}) => {
      const qs = new URLSearchParams({ page });
      if (keyword) qs.set("keyword", keyword);
      return request(`/api/animes?${qs.toString()}`, { signal });
    },
    // 某部番剧的元数据 + 全部磁力资源
    getAnimeResources: (anilistId) =>
      request(`/api/animes/${anilistId}/resources`),
    // 某部番剧的在线播放剧集列表（后端代理采集站/ANi RSS；无资源时 episodes 为空数组）
    // kw 透传用户搜索词：常为简中番名，是采集站搜索命中率最高的关键词
    getAnimeVideos: (anilistId, kw = "") => {
      const qs = kw ? `?kw=${encodeURIComponent(kw)}` : "";
      return request(`/api/animes/${anilistId}/videos${qs}`);
    },
    uploadAvatar: (file) => {
      const fd = new FormData();
      fd.append("file", file);
      return request("/api/avatar", { method: "POST", body: fd, isFormData: true });
    },
  };
})();
