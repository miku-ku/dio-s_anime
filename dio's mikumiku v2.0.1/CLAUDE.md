# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

动漫资源检索 + 用户系统的学习演示站：**FastAPI 后端同时托管 API 和原生 HTML/CSS/JS 前端**（无构建步骤、无前端框架），访问 http://127.0.0.1:8000 即整站。两大模块：

- 动漫检索：后端代理 AniList GraphQL（番剧元数据/封面）+ AnimeGarden 开放 API（磁力资源聚合）
- 用户系统：注册/登录/记住我/个人主页/头像上传/改密/简介，SQLite + PBKDF2 + JWT

## 常用命令

```bash
cd backend
.venv/Scripts/python -m uvicorn main:app --reload    # 启动（Windows venv 在 Scripts/ 不是 bin/）
.venv/Scripts/python -m pip install -r requirements.txt  # 装依赖
```

- **没有测试框架、没有 linter**。验证方式：启动服务后用 curl 打接口；前端 JS 用 `node --check frontend/js/*.js` 做语法检查；页面改动用浏览器走查。
- Swagger 文档在 /docs。
- 环境变量 `SECRET_KEY` 可选（开发用内置默认值，启动会打 warning；生产必须设）。

## 后端架构要点（main.py 是唯一路由文件）

**静态挂载顺序敏感**：`app.mount("/uploads", ...)` 必须在 `app.mount("/", ...)`（前端 catch-all）之前，新增挂载同理。

**鉴权链（跨 auth.py / database.py / main.py / profile.js）**：JWT payload 带 `tv` 声明；`get_current_user` 每次请求查库核对 users 表的 `token_version`；改密接口执行 `token_version + 1`，所有已签发令牌（含当前会话）立即失效。前端 profile.js 据此在改密成功后 clearToken 跳登录页。改鉴权逻辑时四处要一起看。

**数据库迁移模式（database.py）**：`CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 检查 + `ALTER TABLE ADD COLUMN` 给旧库补列。app.db 持久存在不会重建——**加新列必须同时写迁移分支**，否则现有库启动后缺列。

**上游代理的两个坑（注释里也有）**：
- AniList GraphQL 查询用**字符串拼接**构造，别用 f-string（花括号转义易错）
- AnimeGarden 关键词搜索必须用查询参数 `search=`，GET 带 JSON body 会被上游忽略
- 上游响应的 `resp.json()` / `item["id"]` 都要捕 `json.JSONDecodeError` / `KeyError`（anilist_gql 转 502，资源聚合循环里 continue）

**中文标题补全（titleZh，main.py 的 extract_chinese_title / enrich_zh_titles）**：AniList 无中文标题，后端从 AnimeGarden 资源发布标题启发式反推中文名（解析「中文 / Romaji」、下划线、【】括号等形态，跨资源投票）。`/api/animes` 对每部番并发补全：信号量限 6 并发、单项 6s / 整体 12s 超时、内存缓存 `_zh_cache`（anilist_id → 中文名，空串表示查过但推断不出，防反复打上游），任何失败都静默退回原标题；`/api/animes/{id}/resources` 用已聚合的资源顺带提取，零额外上游请求。前端 search.js / anime.js 标题优先级均为 `titleZh || romaji || native || english`。

**在线播放（/api/animes/{id}/videos，双源并联）**：两类源并联、前端一个播放器。**① MacCMS 采集资源站（优先，m3u8）**：`VOD_API_SOURCES` 常量列接口（现为非凡资源，即樱空学园等动漫站的采集上游；域名失效换新域名即可）；协议 `?ac=detail&wd=`，返回 `vod_play_from`/`vod_play_url`（`$$$` 分组、`#` 分集、`集名$url`），只留 m3u8/mp4 直链、优先含直链的组；`_pick_best_vod` 按名称包含+年份打分挑季（年份权重 3 > 精确名 4，别调反，多季番靠年份分季）；**采集站目录是简体，繁中/罗马音一律搜不到**，所以候选词序 = 搜索页透传的 `kw` 参数 > AniList synonyms 里的汉字（无假名）别名 > titleZh 推断 > 其余；请求必须带 `VOD_HEADERS` 浏览器 UA（默认 httpx UA 吃 403）。**② ANi Open RSS（兜底，mp4）**：`ANI_OPEN_MIRRORS` 镜像级联。公共约定：httpx 必须 `follow_redirects=True`（默认不跟随，301 被误判为镜像挂了）；单请求 8s、VOD 整体 12s / ANi 20s、端点总预算 25s（`asyncio.timeout`），VOD 命中即 cancel ANi 任务；集数正则三形态（dash/第X话/裸括号）带防呆（"01-12" 范围、年份、画质数字、上限 1500）；丢弃非 http(s) scheme、相对 URL urljoin；同集去重留 pubDate 较新者；`_video_cache` TTL 30 分钟**只缓存非空结果**（缓存空会把偶发故障锁定半小时）；上游失败一律静默返回空 episodes（前端降级"暂无"），仅 AniList 查无此番才 404。已知短板：无 kw 且 synonyms 无简体时（如热门期繁中资源霸榜的番）采集站搜不中。离线环境返回空属预期。

**头像上传（/api/avatar）是特例**：不用 `UploadFile = File(...)`，而是手动 `await request.form(max_part_size=MAX_AVATAR_SIZE)`——Starlette 1.x 默认单 part 上限 1MB，不显式放宽会让 1~2MB 合法头像直接 500。安全约束：魔数定格式（不信 Content-Type）、分块读取限 2MB、`uuid4().hex` 随机文件名（防按用户 id 枚举）、按 DB 记录的旧文件名删旧图。

**限流（main.py 内 rate_limit）**：内存滑动窗口 + `threading.Lock`（login/register 是 sync 端点跑线程池，锁必需），按「路径|IP」计数，仅单进程有效。挂在路由的 `dependencies=[Depends(rate_limit(10, 60))]`。

## 前端约定（frontend/）

- **页面格局**：index.html = 搜索首页（站点入口，`/` 即它）；profile.html = 个人主页；anime.html 详情；login/register。登录/注册/个人主页入口收在导航右上角的头像下拉菜单里（没有独立的顶部导航链接）。
- **非 module 的普通 script，引入顺序固定：`api.js → util.js → 页面 js`**。5 个 html 都遵守；新页面照抄。详情页在 util.js 与 anime.js 之间额外加载 `js/hls.min.js`（本地文件，m3u8 播放用；mp4 直链用不到）。
- **util.js 是公共层**：renderNav（右上角头像下拉菜单：登录态拉 `/api/me` 显示头像、菜单含个人主页/退出，未登录含登录/注册；401 时 clearToken 降级为游客菜单，断网保留占位图标）、escapeHtml、formatSize/formatDate、showToast、copyMagnet、resourceCardHtml/bindCopyButtons。重复逻辑上移到这里，不要在页面 js 里复制。
- **XSS 不变量**：所有动态 HTML 一律走 `escapeHtml`（或 textContent），历史审查确认全站已做到，新代码必须保持。
- **详情页两栏（anime.html）**：`.detail-wrap`（1280px）内 `.detail-layout` 左播放器（2fr）右磁力列表（1fr），<900px 堆叠。注意全局 `.card { max-width: 380px }` 陷阱：详情页宽卡片（.anime-header / .player-card / .res-card）要显式解除，且 `.res-card` 的解除必须作用域限定在 `.detail-wrap` 内——index.html 兜底直搜列表复用同一卡片模板，全局放开会连累它。卡片链接带 `&kw=` 透传用户搜索词（encodeURIComponent），详情页读 `kw` 参数交给 /videos——这是采集站简中搜索命中率最高的关键词，别弄丢。
- **api.js 的错误契约**：抛出的 Error 带 `.status`——`0` = 网络层错误（断网），`401` = 凭证失效，其余为 HTTP 状态。profile.js 和 renderNav 依赖这个区分：断网保留 token 提示重试，仅 401 才清 token。fetch 支持透传 `signal`（AbortController），search.js 用它防翻页竞态。
- **搜索状态在 URL 里**（search.js）：keyword/type/page 用 `history.replaceState` 同步到 query，刷新/返回可恢复；anime.html 的返回链接用 `history.back()`（带同源 referrer 守卫）回到搜索现场。改搜索流程时保持这个机制。
- **类型筛选语义**：`type` 是资源级属性，AniList 番剧卡片没有此维度——搜索页（index.html）选了非空类型就切换为资源直搜模式（调 `API.searchAnime`），type 为空才走 AniList 卡片流。

## 环境与数据注意事项

- Windows + Git Bash：bash 输出中文会因 GBK 编码显示乱码，属正常现象——看 HTTP 状态码/ASCII 部分判断结果即可。
- `backend/app.db` 和 `backend/uploads/` 是真实用户数据（已有 bob/carol/chisasa 三个账号），测试时**注册临时账号、测完删除**，不要清库或动既有用户的头像文件。
- 项目尚未 `git init`（.gitignore 已备好：.venv/、__pycache__/、app.db、uploads/ 不入库）。
- 端口固定 8000；CORS 白名单只含本机 8000 端口的两个 origin（main.py），单独起前端服务时需手动加。
