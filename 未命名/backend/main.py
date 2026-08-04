"""FastAPI 后端入口：用户注册 / 登录 / 获取当前用户。"""
import json
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import jwt as pyjwt
from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import MultiPartException

from auth import create_token, decode_token, hash_password, verify_password
from database import get_db, init_db
from models import (
    BioUpdateRequest,
    ChangePasswordRequest,
    LoginRequest,
    TokenOut,
    UserCreate,
    UserOut,
)

# 头像上传相关配置
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)  # 保证 StaticFiles 挂载时目录已存在
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2MB


def _sniff_image_ext(head: bytes) -> str | None:
    """按文件魔数判定真实图片格式，返回扩展名；不是受支持的图片返回 None。

    不看 Content-Type——它可以被客户端任意伪造。
    """
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()  # 启动时建表
    UPLOAD_DIR.mkdir(exist_ok=True)  # 头像存储目录
    yield


app = FastAPI(title="用户注册登录示例 API", lifespan=lifespan)

# 允许跨域（前端由 FastAPI 自身托管时同源、其实用不到；单独起前端服务时需在此加对应 origin）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 简易限流（内存滑动窗口，无第三方依赖） ----------
# 注意：仅适用于单进程部署（如 --reload 开发模式）；多 worker 时各进程独立计数，
# 且进程重启即清零。生产环境的限流应放在网关/反向代理层。

_rate_buckets: dict[str, deque] = {}
_rate_lock = threading.Lock()  # login/register 是同步端点，跑在线程池里，必须加锁


def _rate_allowed(key: str, limit: int, window: float) -> bool:
    """判断 key 在 window 秒内是否还有可用额度（顺带清理过期记录）。"""
    now = time.monotonic()
    with _rate_lock:
        dq = _rate_buckets.setdefault(key, deque())
        while dq and now - dq[0] >= window:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


def rate_limit(limit: int = 10, window: int = 60):
    """按「路径 + 客户端 IP」限流，超限返回 429。

    用法：@app.post(..., dependencies=[Depends(rate_limit(10, 60))])
    """

    def dependency(request: Request):
        ip = request.client.host if request.client else "?"
        if not _rate_allowed(f"{request.url.path}|{ip}", limit, window):
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    return dependency


@app.exception_handler(MultiPartException)
async def _multipart_error_handler(_: Request, exc: MultiPartException):
    """multipart 解析失败（如单个文件超过 max_part_size）→ 中文 400，避免裸 500。"""
    return JSONResponse(status_code=400, content={"detail": "图片大小不能超过 2MB"})


# ---------- 鉴权依赖 ----------

def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """从 Authorization: Bearer <token> 头中解析并校验当前用户。

    除验签外还查库核对 token_version：用户改密后版本号 +1，
    之前签发的所有令牌（即使未过期）立即失效。
    老令牌没有 tv 字段时按 0 处理，与迁移后的默认值兼容。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="无效的登录凭证")
    if not payload.get("username") or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="无效的登录凭证")

    row = _find_user(payload["username"])
    if row is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if row["token_version"] != int(payload.get("tv", 0)):
        raise HTTPException(status_code=401, detail="登录凭证已失效，请重新登录")
    return payload


def _find_user(username: str):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT id, username, password_hash, avatar, bio, token_version, created_at"
            " FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()


def _user_out(row) -> UserOut:
    """把数据库行转成返回给前端的用户信息。"""
    return UserOut(
        id=row["id"],
        username=row["username"],
        avatar=_avatar_url(row),
        bio=row["bio"] or "",
        created_at=row["created_at"],
    )


def _avatar_url(row) -> str | None:
    """把数据库里的头像文件名转成可访问的 URL。"""
    return f"/uploads/{row['avatar']}" if row["avatar"] else None


# ---------- 接口 ----------

@app.post(
    "/api/register",
    response_model=TokenOut,
    dependencies=[Depends(rate_limit(10, 60))],
)
def register(body: UserCreate):
    """注册新用户，成功后直接返回登录令牌。"""
    if _find_user(body.username):
        raise HTTPException(status_code=400, detail="用户名已存在")

    conn = get_db()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (body.username, hash_password(body.password)),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # 并发竞态兜底：两个请求同时通过上面的重名检查后抢先插入
            raise HTTPException(status_code=400, detail="用户名已存在")
        user_id = cur.lastrowid
    finally:
        conn.close()

    return TokenOut(
        token=create_token(user_id, body.username, remember=body.remember),
        user=UserOut(id=user_id, username=body.username),
    )


@app.post(
    "/api/login",
    response_model=TokenOut,
    dependencies=[Depends(rate_limit(10, 60))],
)
def login(body: LoginRequest):
    """校验用户名密码，成功则返回登录令牌。"""
    row = _find_user(body.username)
    # 注意：用户不存在和密码错误返回同样的提示，避免泄露用户名是否存在
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    return TokenOut(
        token=create_token(
            row["id"], row["username"], remember=body.remember, tv=row["token_version"]
        ),
        user=_user_out(row),
    )


@app.get("/api/me", response_model=UserOut)
def me(current_user: dict = Depends(get_current_user)):
    """返回当前登录用户信息（需要携带有效令牌）。"""
    row = _find_user(current_user["username"])
    if row is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return _user_out(row)


@app.post("/api/bio", response_model=UserOut)
def update_bio(
    body: BioUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """修改当前登录用户的个人简介（最多 200 字）。"""
    bio = body.bio.strip()
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET bio = ? WHERE id = ?",
            (bio, int(current_user["sub"])),
        )
        conn.commit()
    finally:
        conn.close()

    row = _find_user(current_user["username"])
    return _user_out(row)


@app.post("/api/change-password")
def change_password(
    body: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    """修改当前登录用户的密码，需校验原密码。"""
    row = _find_user(current_user["username"])
    if row is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确")

    conn = get_db()
    try:
        # token_version + 1：改密后所有已签发令牌（包括当前会话）立即失效
        conn.execute(
            "UPDATE users SET password_hash = ?, token_version = token_version + 1"
            " WHERE id = ?",
            (hash_password(body.new_password), row["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return {"message": "密码修改成功，请重新登录"}


@app.post("/api/avatar")
async def upload_avatar(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """上传/更换当前登录用户的头像（JPG/PNG/GIF/WebP，≤2MB）。

    安全要点：
    - 手动 request.form(max_part_size=...)：Starlette 1.x 默认单 part 上限仅 1MB，
      不显式放宽的话 1~2MB 的合法头像会在解析层抛 MultiPartException 变成 500；
      超限的请求由下面注册的 exception_handler 转成中文 400。
    - 魔数校验：按文件内容判定格式，不信任可伪造的 Content-Type。
    - 分块读取：超过 2MB 立即中止，不把大文件整个读进内存。
    - 随机文件名：防止按用户 id 枚举遍历所有人的头像。
    """
    form = await request.form(max_part_size=MAX_AVATAR_SIZE)
    try:
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(status_code=400, detail="缺少上传文件")

        chunks: list[bytes] = []
        total = 0
        while chunk := await file.read(64 * 1024):
            total += len(chunk)
            if total > MAX_AVATAR_SIZE:
                raise HTTPException(status_code=400, detail="图片大小不能超过 2MB")
            chunks.append(chunk)
        data = b"".join(chunks)

        ext = _sniff_image_ext(data[:12])
        if ext is None:
            raise HTTPException(
                status_code=400,
                detail="文件内容与图片格式不符（仅支持 JPG/PNG/GIF/WebP）",
            )
    finally:
        await form.close()

    filename = uuid.uuid4().hex + ext  # 随机名，防枚举
    (UPLOAD_DIR / filename).write_bytes(data)

    conn = get_db()
    try:
        old = conn.execute(
            "SELECT avatar FROM users WHERE id = ?", (int(current_user["sub"]),)
        ).fetchone()
        conn.execute(
            "UPDATE users SET avatar = ? WHERE id = ?",
            (filename, int(current_user["sub"])),
        )
        conn.commit()
    finally:
        conn.close()

    # 清理旧头像文件（按数据库记录的旧文件名删，兼容历史 avatar_{id}.ext 命名）
    if old and old["avatar"]:
        (UPLOAD_DIR / old["avatar"]).unlink(missing_ok=True)

    return {"avatar": f"/uploads/{filename}"}


# ---------- 动漫资源检索（代理 AnimeGarden 开放 API） ----------
ANIMEGARDEN_API = "https://api.animes.garden"
# 番剧元数据/封面来源（Bangumi 需翻墙，故用 AniList 开放 GraphQL）
ANILIST_API = "https://graphql.aniList.co"
ANILIST_HEADERS = {"User-Agent": "AnimeSearchSite/1.0 (local demo)"}

ANIME_FIELDS = """
  id
  title { romaji native english }
  coverImage { large medium color }
  bannerImage
  episodes
  seasonYear
  format
"""

# 用字符串拼接构造 GraphQL，避免 f-string 花括号转义出错
# 按关键词分页搜索番剧
ANILIST_SEARCH_Q = (
    "query($search:String,$page:Int){"
    "Page(page:$page,perPage:20){"
    "pageInfo{total currentPage hasNextPage}"
    "media(search:$search,type:ANIME,sort:SEARCH_MATCH){"
    + ANIME_FIELDS +
    "}}}"
)

# 无关键词时的默认推荐（按热度）
ANILIST_TRENDING_Q = (
    "query($page:Int){"
    "Page(page:$page,perPage:20){"
    "pageInfo{total currentPage hasNextPage}"
    "media(type:ANIME,sort:TRENDING_DESC){"
    + ANIME_FIELDS +
    "}}}"
)

# 按 id 取单部番剧
ANILIST_MEDIA_Q = (
    "query($id:Int){Media(id:$id,type:ANIME){" + ANIME_FIELDS + "}}"
)


async def anilist_gql(query: str, variables: dict) -> dict:
    """调用 AniList GraphQL，返回 data 字段；出错抛 502。"""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            ANILIST_API,
            json={"query": query, "variables": variables},
            headers=ANILIST_HEADERS,
        )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=502, detail="AniList 返回了无法解析的数据")
    if payload.get("errors"):
        raise HTTPException(status_code=502, detail="AniList 接口返回错误")
    return payload.get("data") or {}


@app.get("/api/anime/search")
async def search_anime(
    keyword: str = "",
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=50),
    res_type: str = Query(default="", alias="type"),
):
    """代理转发 AnimeGarden 的资源搜索接口。

    走后端代理而非浏览器直连：① 规避跨域；② 统一在服务端控制超时与错误提示。
    AnimeGarden 的关键词搜索用查询参数 search=... 即可（实测 GET 携带 JSON body
    会被服务端忽略，故不用 body 方式）。
    """
    params = {"page": page, "pageSize": pageSize}
    if keyword.strip():
        params["search"] = keyword.strip()
    if res_type:
        params["type"] = res_type

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{ANIMEGARDEN_API}/resources", params=params)
            resp.raise_for_status()
            try:
                return resp.json()
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=502, detail="动漫资源接口返回了无法解析的数据"
                )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="动漫资源接口响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502, detail=f"动漫资源接口返回异常（{e.response.status_code}）"
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="无法连接动漫资源接口，请检查网络")


@app.get("/api/animes")
async def search_animes(
    keyword: str = "",
    page: int = Query(default=1, ge=1),
):
    """按关键词搜索番剧（AniList），返回带封面的番剧卡片列表。

    无关键词时返回热门番剧，方便首页浏览。
    """
    kw = keyword.strip()
    try:
        if kw:
            data = await anilist_gql(ANILIST_SEARCH_Q, {"search": kw, "page": page})
        else:
            data = await anilist_gql(ANILIST_TRENDING_Q, {"page": page})
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="无法连接 AniList，请检查网络")

    pg = data.get("Page") or {}
    return {"animes": pg.get("media") or [], "pageInfo": pg.get("pageInfo") or {}}


@app.get("/api/animes/{anilist_id}/resources")
async def anime_resources(anilist_id: int):
    """取某部番剧的元数据 + 它在 AnimeGarden 上的全部磁力资源。

    用 AniList 的罗马音/日文/英文标题依次去 AnimeGarden 搜索并合并去重，
    以便尽量覆盖不同字幕组的命名。
    """
    try:
        data = await anilist_gql(ANILIST_MEDIA_Q, {"id": anilist_id})
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="无法连接 AniList，请检查网络")
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="未找到该番剧")

    title = media.get("title") or {}
    candidates = [title.get("romaji"), title.get("native"), title.get("english")]
    # 去重并保序，去掉空值
    seen, terms = set(), []
    for t in candidates:
        if t and t not in seen:
            seen.add(t)
            terms.append(t)

    merged: dict = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        for term in terms:
            try:
                resp = await client.get(
                    f"{ANIMEGARDEN_API}/resources",
                    params={"search": term, "pageSize": 30},
                )
                if resp.status_code == 200:
                    for item in resp.json().get("resources", []):
                        merged[item["id"]] = item
            # 单个关键词请求失败/数据残缺不拖垮整体，跳过继续
            except (httpx.HTTPError, json.JSONDecodeError, KeyError):
                continue
            if len(merged) >= 30:  # 够了就不再请求，减少上游压力
                break

    resources = sorted(
        merged.values(), key=lambda x: x.get("createdAt") or "", reverse=True
    )
    return {"anime": media, "resources": resources}


# ---------- 静态资源 ----------
# 头像文件托管（放在 / 之前，避免被前端 catch-all 拦截）
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# 托管前端静态页面：访问 http://localhost:8000 即可打开首页
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
