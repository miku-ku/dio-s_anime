# 动漫资源检索 + 用户系统（FastAPI + SQLite + JWT）

一个完整的前后端交互示例，包含两大模块：

1. **动漫检索（卡片式）**：搜索 [AniList](https://anilist.co) 获取番剧元数据与**海报封面**，
   以卡片网格展示；点进某部番剧，后端用它的罗马音/日文名去
   [AnimeGarden](https://animes.garden) 聚合出该番**全部磁力链**，整合在一页，一键复制。
   纯中文关键词 AniList 匹配不到时，自动回退到资源站直搜。
2. **在线播放**：详情页内置选集 + 播放器，片源来自 **ANi 开放 RSS** 的 MP4 直链
   （以当季新番为主，未收录的番剧自动降级为"暂无在线播放"，磁力下载不受影响）。
3. **用户系统**：注册、登录（记住我）、个人主页（左侧改头像/改密码，中间弹窗编辑个人简介）。

> ⚠️ 资源来自第三方聚合接口，涉及版权灰色地带，仅作学习演示，请勿公开运营：
> BT/磁力来自 **AnimeGarden**；在线播放直链来自 **MacCMS 采集资源站**（现配置为非凡资源）
> 与 **ANi 开放 RSS**（第三方字幕组公开接口）。
>
> 数据源说明：番剧封面/元数据用 **AniList**（Bangumi 在国内需翻墙，故弃用）；
> 磁力资源用 **AnimeGarden** 开放 API；在线播放优先用 **MacCMS 采集接口**（m3u8，
> `backend/main.py` 的 `VOD_API_SOURCES`，域名失效可换），兜底 **ANi Open RSS**
> （mp4，`ANI_OPEN_MIRRORS`，可按自己网络增删）。

## 项目结构

```
未命名/
├── backend/                 # FastAPI 后端
│   ├── main.py             # 应用入口：路由 + 限流 + 托管前端静态文件
│   ├── database.py         # SQLite 连接与建表（含旧库自动补列迁移）
│   ├── auth.py             # 密码哈希（PBKDF2）+ JWT 签发/校验
│   ├── models.py           # Pydantic 请求/响应模型
│   ├── requirements.txt    # 依赖清单
│   └── app.db              # SQLite 数据库（首次启动自动生成）
└── frontend/               # 前端静态页面（由 FastAPI 托管）
    ├── index.html          # 动漫搜索首页（站点入口，访问 / 即它）：番剧卡片网格（海报）+ 类型筛选 + 中文兜底直搜
    ├── anime.html          # 番剧详情：封面/信息 + 左侧在线播放（选集+播放器）+ 右侧磁力链整合
    ├── profile.html        # 个人主页：左栏设置（头像/改密）+ 中间简介（弹窗编辑）
    ├── login.html          # 登录页（含“记住我”）
    ├── register.html       # 注册页（含“记住我”）
    ├── favicon.svg         # 站点图标
    ├── css/style.css
    └── js/
        ├── api.js          # fetch 封装：自动带 JWT、FormData 上传、错误中文化、AbortSignal 支持
        ├── util.js         # 公共工具：导航渲染、HTML 转义、格式化、toast、复制、资源卡片模板
        ├── search.js / anime.js / profile.js / login.js / register.js
        └── hls.min.js      # hls.js 播放库（本地文件，详情页播 m3u8 用）
```

后端新增 `backend/uploads/` 目录用于存放上传的头像（首次启动自动创建）。

## 如何运行

```bash
cd backend

# 1. 创建虚拟环境（已完成可跳过）
python -m venv .venv

# 2. 安装依赖（已完成可跳过）
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt              # macOS/Linux

# 3. 启动服务
.venv/Scripts/python -m uvicorn main:app --reload         # Windows
# .venv/bin/python -m uvicorn main:app --reload          # macOS/Linux
```

然后浏览器打开 **http://127.0.0.1:8000** 即可（前端和 API 都由这一个服务提供）。

- 交互式 API 文档（Swagger）：http://127.0.0.1:8000/docs

## 前后端交互流程

1. 前端表单提交 → `fetch('/api/register' | '/api/login')` 发送 JSON
2. 后端校验用户名/密码 → 写入 SQLite（密码只存哈希）→ 返回 **JWT 令牌**
3. 前端把令牌存入 `localStorage`，之后每次请求都在 `Authorization: Bearer <token>` 头中携带
4. 导航栏头像和个人主页加载时调用 `GET /api/me`，后端校验令牌并返回当前用户信息
5. 退出登录 = 前端清除 `localStorage` 中的令牌

JWT 本身是无状态的，但本实现通过 users 表的 `token_version` 列实现了**有限吊销**：
令牌 payload 里带版本号 `tv`，每次请求后端都会核对；**改密时版本号 +1，
所有已签发令牌（包括当前会话）立即失效**，前端检测到 401 后清除本地令牌并跳回登录页。

## API 一览

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| GET  | `/api/animes` | 番剧搜索（代理 AniList，带封面）；无 `keyword` 时返回热门番 | 无 |
| GET  | `/api/animes/{id}/resources` | 某番剧元数据 + 聚合它在 AnimeGarden 的全部磁力资源 | 无 |
| GET  | `/api/animes/{id}/videos` | 某番剧的在线播放剧集列表：MacCMS 采集站（m3u8）优先、ANi RSS（mp4）兜底，可带 `kw` 搜索词参数；上游无资源时返回空列表 | 无 |
| GET  | `/api/anime/search` | 资源直搜（代理 AnimeGarden，中文兜底/类型筛选用），参数 `keyword`/`type`/`page`/`pageSize` | 无 |
| POST | `/api/register` | 注册，成功直接返回令牌（`remember:true` 时有效期 30 天）；限流 10 次/分钟/IP | 无 |
| POST | `/api/login` | 登录，返回令牌（`remember:true` 时有效期 30 天）；限流 10 次/分钟/IP | 无 |
| GET  | `/api/me` | 获取当前登录用户（含头像 URL、简介） | 需要 Bearer 令牌 |
| POST | `/api/change-password` | 修改密码（需校验原密码） | 需要 Bearer 令牌 |
| POST | `/api/avatar` | 上传/更换头像（JPG/PNG/GIF/WebP，≤2MB，multipart） | 需要 Bearer 令牌 |
| POST | `/api/bio` | 修改个人简介（≤200 字） | 需要 Bearer 令牌 |
| GET  | `/uploads/<文件名>` | 头像静态文件 | 无 |

## 功能说明

- **动漫检索（卡片式）**：`/api/animes` 代理 AniList GraphQL 搜番（带海报），前端渲染卡片网格；
  点卡片进 `/api/animes/{id}/resources`，后端取该番罗马音/日文名去 AnimeGarden 搜索并**合并去重**
  （按资源 id），把整部番的磁力链整合到一页。纯中文 AniList 匹配不到时前端自动回退资源站直搜。
  搜索栏还有**类型筛选**（动画/合集/特摄/漫画/音乐/游戏）：type 是资源级属性、AniList
  番剧卡片没有这个维度，所以选中类型后自动切换为资源直搜模式。
  搜索状态（关键词/类型/页码）会同步到 URL，刷新页面或从详情页返回都能恢复现场。
  资源不落库，实时请求上游；封面图由浏览器直接从 AniList CDN 加载，加载失败显示首字占位。
  点“复制磁力链”写入剪贴板（`navigator.clipboard`，含降级方案）。
  > 踩坑记录：① AnimeGarden 关键词搜索实测 **GET 携带 JSON body 会被忽略**，须用查询参数 `search=`；
  > ② AniList 的 GraphQL 用 f-string 拼花括号易少数层级，改用字符串拼接。
- **在线播放**：详情页左侧选集 + 播放器，右侧磁力资源。后端
  `/api/animes/{id}/videos` 双源并联：**MacCMS 采集资源站**（`VOD_API_SOURCES`，
  现配置非凡资源——也就是很多动漫站采集数据的上游，曲库广、老番也有）返回 m3u8，
  前端用 hls.js 播放；**ANi Open RSS**（`ANI_OPEN_MIRRORS`）兜底返回 mp4 直链，
  原生 `<video>` 播放。采集站目录是简体中文，所以检索词按「搜索页透传的关键词 >
  AniList 简体同义词 > titleZh 推断的中文名 > 罗马音/日文」的优先级尝试，
  并按年份给多季番选对季。只留 m3u8/mp4 直链、按集数去重排序（无集数的排末尾标 SP），
  结果缓存 30 分钟（只缓存非空）。上游未收录/故障时静默返回空列表，前端显示
  "暂无在线播放资源"，磁力下载不受影响；磁力与播放两个数据源并行加载、互不阻塞。
- **记住我**：登录/注册时勾选，令牌有效期延长到 30 天并存入 `localStorage`（关浏览器后仍保留）；
  不勾选则存入 `sessionStorage`（关浏览器即需重新登录）。
- **个人主页**：登录后进入。左栏可更换头像、修改密码；中间是个人简介，点“修改”弹出
  弹窗（textarea + 实时字数统计，最多 200 字），保存后即时回显。弹窗支持点遮罩、按
  `Esc` 或右上角 × 关闭。加载中显示 loading 提示；网络异常时保留令牌并提示重试
  （断网 ≠ 登出），仅 401 才清除令牌。
- **修改密码**：输入原密码和新密码，校验原密码正确后更新；改密后 `token_version` +1，
  **所有已签发令牌（包括当前会话）立即失效**，前端自动跳回登录页。
- **头像上传**：选择图片后前端先预检类型/大小，再以 `multipart/form-data` 上传；
  后端按**文件魔数**判定真实格式（不信任可伪造的 Content-Type）、分块读取限制 2MB，
  存到 `backend/uploads/` 并用**随机文件名**（防止按用户 id 枚举遍历所有人的头像），
  替换时自动删除旧文件；前端展示时用时间戳参数避免浏览器缓存旧图。

## 安全说明 / 生产环境建议

已实现的防护：

- **令牌可吊销**：users 表 `token_version` 列 + JWT `tv` 声明，改密后所有旧令牌立即失效
- **限流**：`/api/login`、`/api/register` 每 IP 每分钟 10 次，超限返回 429
  （内存滑动窗口计数，**仅适用于单进程**：多 worker 部署时各进程独立计数，重启即清零）
- **头像加固**：魔数校验（防伪造扩展名）、分块读取限 2MB、随机文件名（防枚举）、替换删旧
- **CORS 收紧**：只允许本机 8000 端口的 origin；前端同源托管不受影响，
  若要单独起前端 dev server，需在 `main.py` 的 `allow_origins` 里手动添加对应地址
- 未设 `SECRET_KEY` 环境变量时，启动日志会打印醒目的警告

生产环境还建议：

- 通过环境变量 `SECRET_KEY` 设置一个随机长密钥（内置默认值仅供本地开发）：
  `set SECRET_KEY=一串随机长字符串`（Windows）或 `export SECRET_KEY=...`
- 把 PBKDF2 换成 `bcrypt`/`argon2`，并考虑用 HttpOnly Cookie 代替 localStorage 存令牌
- 上线时通过 HTTPS 部署，避免令牌在网络上明文传输
- 限流放到网关/反向代理层（如 Nginx），并补充账号级锁定策略
