"""FastAPI 后端入口：用户注册 / 登录 / 获取当前用户。"""
import asyncio
import itertools
import json
import re
import sqlite3
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from contextlib import asynccontextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

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
  synonyms
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


# ---------- 中文标题提取（从 AnimeGarden 资源发布标题反推） ----------
# AniList 不提供中文标题，但 dmhy 系的资源发布标题几乎都带中文名，常见形态：
#   "[字幕组] 中文标题 / Romaji - 01 [1080p][GB]"
#   "[字幕组][中文标题_Romaji][第01话]"
#   "【字幕组】【24年1月新番】【中文标题 日文标题】【01】"
# 思路：从每条资源标题里抽候选，跨多条资源投票出最可能的一个。
# 属启发式解析，推断不出的番剧保持显示 AniList 原标题。

_RE_CJK = re.compile(r"[一-鿿]")    # 汉字
_RE_KANA = re.compile(r"[぀-ヿ]")   # 假名（区分中/日标题）
_RE_BRACKETS = re.compile(r"[\[【（(][^\]】）)]*[\]】）)]")
# 含这些词的片段是资源属性（字幕组/画质/新番/集数），不是番剧名
_ZH_DENY = re.compile(
    r"字幕组|字幕|新番|合集|连载|更新|话|話|MP4|MKV|AVC|HEVC|BDRip|WebRip|1080|720|480",
    re.IGNORECASE,
)


def _clean_zh(seg: str) -> str:
    """把一段候选文本清洗成中文标题；不像中文标题的返回空串。"""
    seg = seg.strip(" \t-–—~～·:：,，!！?？'\"")
    if not seg or not _RE_CJK.search(seg):
        return ""
    if _RE_KANA.search(seg):
        # 中日混排（如「迷宫饭 ダンジョン飯」）→ 取第一段纯中文
        seg = next(
            (p for p in seg.split() if _RE_CJK.search(p) and not _RE_KANA.search(p)),
            "",
        ).strip()
    if len(seg) < 2 or len(seg) > 40 or _ZH_DENY.search(seg):
        return ""
    return seg


def _zh_candidates(title: str) -> list[str]:
    """从一条资源发布标题抽出候选中文标题（按可能性排序）。"""
    # 括号外主体 + 各括号内容，都按 " / "、"/"、"_"、"|" 切段
    parts = [_RE_BRACKETS.sub(" ", title)]
    parts += [
        m.group(1) for m in re.finditer(r"[【（(]([^【】（）()\[\]]+)[】）)]", title)
    ]
    cands: list[str] = []
    for part in parts:
        for seg in re.split(r"\s*/\s*|_|｜|\|", part):
            c = _clean_zh(seg)
            if c:
                cands.append(c)
    return cands


def extract_chinese_title(resources: list[dict], known_titles) -> str | None:
    """跨多条资源的发布标题投票出最可能的中文标题；推断不出返回 None。

    known_titles 传 AniList 的 romaji/native/english——它们本就用于搜索匹配，
    不算中文标题。
    """
    known = {t.strip().lower() for t in known_titles if t}
    weights: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for res in resources:
        # 一条资源内去重；dmhy 惯例中文标题在 "/" 前，首个候选记双倍权重
        for i, cand in enumerate(dict.fromkeys(_zh_candidates(res.get("title") or ""))):
            if cand.lower() in known:
                continue
            if cand not in weights:
                first_seen[cand] = len(first_seen)
            weights[cand] = weights.get(cand, 0) + (2 if i == 0 else 1)
    if not weights:
        return None
    return max(weights, key=lambda c: (weights[c], -first_seen[c]))


# 中文名缓存：anilist_id → 中文标题（"" 表示查过但推断不出，避免反复打上游）。
# 内存缓存在单进程内有效，重启清零。
_zh_cache: dict[int, str] = {}
_zh_sem = asyncio.Semaphore(6)  # 并发上限，别对 AnimeGarden 一次性打太多请求


async def _fetch_zh_for_term(
    client: httpx.AsyncClient, term: str, known: list
) -> str | None:
    """用关键词搜 AnimeGarden，从结果标题里投票中文标题。"""
    resp = await client.get(
        f"{ANIMEGARDEN_API}/resources", params={"search": term, "pageSize": 8}
    )
    if resp.status_code != 200:
        return None
    try:
        items = resp.json().get("resources") or []
    except (json.JSONDecodeError, AttributeError):
        return None
    return extract_chinese_title(items, known)


async def enrich_zh_titles(animes: list[dict]) -> None:
    """给 AniList 结果并发补 titleZh 字段；任何失败都静默退回原标题。

    每部番先试罗马音标题、无果再试 native 标题（覆盖纯中文命名的国产番）。
    整体加 12 秒预算：超时就放弃未完成的补全，已补上的保留，不拖垮搜索响应。
    """
    todo = []
    for a in animes:
        aid = a.get("id")
        if not isinstance(aid, int):
            continue
        if aid in _zh_cache:
            if _zh_cache[aid]:
                a["titleZh"] = _zh_cache[aid]
            continue
        todo.append(a)
    if not todo:
        return

    async with httpx.AsyncClient(timeout=6.0) as client:

        async def enrich_one(a: dict) -> None:
            t = a.get("title") or {}
            known = [x for x in (t.get("romaji"), t.get("native"), t.get("english")) if x]
            zh = None
            tried = set()
            for term in (t.get("romaji"), t.get("native")):
                if not term or term in tried:
                    continue
                tried.add(term)
                try:
                    async with _zh_sem:
                        zh = await asyncio.wait_for(
                            _fetch_zh_for_term(client, term, known), timeout=6.0
                        )
                except (httpx.HTTPError, asyncio.TimeoutError):
                    zh = None
                if zh:
                    break
            _zh_cache[a["id"]] = zh or ""
            if zh:
                a["titleZh"] = zh

        try:
            async with asyncio.timeout(12):
                await asyncio.gather(*(enrich_one(a) for a in todo))
        except asyncio.TimeoutError:
            pass  # 整体超时：保留已补全的部分


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
    animes = pg.get("media") or []
    # 并发补全中文标题（来自 AnimeGarden 资源标题反推）；失败不影响原有结果
    await enrich_zh_titles(animes)
    return {"animes": animes, "pageInfo": pg.get("pageInfo") or {}}


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
    # 顺带从这些资源标题里反推中文标题（零额外上游请求），推断不出就不加该字段
    zh = extract_chinese_title(resources, terms)
    if zh:
        media["titleZh"] = zh
    return {"anime": media, "resources": resources}


# ---------- 在线播放（代理 ANi Open RSS：字幕组 ANi 的开放 RSS） ----------
# 每条 <item> 是一集番剧，<enclosure> 携带 MP4 直链（H.264+AAC，浏览器原生可播）。
# ANi 曲库以新番为主、标题多为繁中；AniList 只有罗马音/日文/英文，所以候选词
# 里会补一个 titleZh 推断出的中文名（简体对繁中库是 best-effort，可能匹配不上）。

ANI_OPEN_MIRRORS = [  # 级联尝试，用户可按自己网络增删
    "https://api.ani.rip",
    "https://aniapi.v300.eu.org",
]
VIDEO_CACHE_TTL = 30 * 60
# anilist_id → (monotonic 时间戳, 响应 dict)。只缓存「非空」成功结果：
# 镜像本就不稳，缓存空结果会把一次偶发失败错误地锁定半小时。
_video_cache: dict[int, tuple[float, dict]] = {}

# 集数正则三形态：
#   "[ANi] 葬送的芙莉蓮 - 07 [1080P][Baha][WEB-DL].mp4"（- 07 / - 12v2）
#   "第07话/話/集"；裸括号 "[05]"
# dash 形态要求分隔符前有空白，"01-12" 这种范围才不会被误抽成 12；
# 括号形态用负向前瞻跳过 [1080] 等画质数字；上限 1500 防把年份当集数，
# 同时兼容柯南级四位数集数。
_EP_DASH = re.compile(r"\s[-–—～~]\s*(\d{1,4})(?:v\d+)?(?=\s|\[|【|$)")
_EP_CN = re.compile(r"第\s*(\d{1,4})\s*[话話集]")
_EP_BRACKET = re.compile(r"[\[【](?!1080|720|480|2160|4[Kk])(\d{1,3})[\]】]")


def extract_episode(title: str) -> int | None:
    """从资源发布标题里抽集数；抽不出返回 None。"""
    for pat in (_EP_DASH, _EP_CN, _EP_BRACKET):
        m = pat.search(title)
        if m:
            ep = int(m.group(1))
            if 1 <= ep <= 1500:
                return ep
    return None


def parse_ani_rss(data: bytes, base: str) -> list[dict]:
    """解析 ANi Open RSS（传 bytes 让 ElementTree 自己处理 XML 声明编码）。

    只留 video/mp4（或 .mp4 后缀）——mkv 等格式浏览器原生播不了；
    丢弃非 http(s) scheme；相对 URL urljoin 回 base；pubDate 解析失败记 ts=0。
    非法 XML 抛 ET.ParseError，由调用方捕获。
    """
    root = ET.fromstring(data)
    eps = []
    for item in itertools.islice(root.iter("item"), 300):  # 超长番防呆
        enc = item.find("enclosure")
        if enc is None:
            continue
        url = (enc.get("url") or "").strip()
        enc_type = (enc.get("type") or "").lower()
        if not url or not (enc_type.startswith("video/mp4") or url.lower().endswith(".mp4")):
            continue
        url = urljoin(base + "/", url)
        if not url.lower().startswith(("http://", "https://")):
            continue
        title = (item.findtext("title") or "").strip()
        raw_date = (item.findtext("pubDate") or "").strip()
        try:
            ts = parsedate_to_datetime(raw_date).timestamp()
        except (TypeError, ValueError):
            ts = 0.0
        length = enc.get("length")
        try:
            size = int(length) if length else None
        except ValueError:
            size = None
        eps.append(
            {
                "episode": extract_episode(title),
                "title": title,
                "url": url,
                "size": size,
                "pubDate": raw_date,
                "_ts": ts,  # 内部字段，供去重排序，返回前剥掉
            }
        )
    return eps


def dedupe_sort_episodes(items: list[dict]) -> list[dict]:
    """按集数去重（同集保留 pubDate 较新者）升序；无集数的按 URL 去重排末尾。"""
    best: dict[int, dict] = {}
    no_ep: dict[str, dict] = {}
    for it in items:
        if it["episode"] is None:
            bucket, key = no_ep, it["url"]
        else:
            bucket, key = best, it["episode"]
        if key not in bucket or it["_ts"] > bucket[key]["_ts"]:
            bucket[key] = it
    ordered = sorted(best.values(), key=lambda x: x["episode"])
    ordered += sorted(no_ep.values(), key=lambda x: -x["_ts"])
    return [{k: v for k, v in it.items() if k != "_ts"} for it in ordered]


async def fetch_ani_episodes(terms: list[str]) -> tuple[list[dict], str]:
    """按 镜像×关键词 顺序搜 ANi RSS，首个非空命中即返回 (episodes, 镜像base)。

    follow_redirects 必须开：httpx 默认不跟随重定向，301 的镜像会被误判为挂了。
    单项 8s 超时；整体 20s 预算，超时返回空，不拖垮详情页。
    """
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        try:
            async with asyncio.timeout(20):
                for base in ANI_OPEN_MIRRORS:
                    for term in terms:
                        try:
                            resp = await client.get(
                                f"{base}/", params={"anime_name": term}
                            )
                            resp.raise_for_status()
                            items = parse_ani_rss(resp.content, base)
                        except (httpx.HTTPError, ET.ParseError, ValueError, UnicodeDecodeError):
                            continue
                        if items:
                            return dedupe_sort_episodes(items), base
        except asyncio.TimeoutError:
            pass  # 总预算耗尽：按无资源处理
    return [], ""


# ---------- 在线播放源二：MacCMS 采集资源站 ----------
# 樱空学园这类动漫站本身就是从这些上游资源站采集数据的（目录+播放地址都存在
# 上游的库里），上游的采集接口天然开放、无人机验证。这里直接对接上游，
# 覆盖面比 ANi 广得多（老番也有），播放地址多为 m3u8（前端用 hls.js 播）。
# 接口协议：GET {api}?ac=detail&wd=关键词 → JSON，list[].vod_play_from 与
# vod_play_url 用 "$$$" 分隔多个播放组，每组内 "#" 分隔各集、"集名$url" 格式。
VOD_API_SOURCES = [  # 可增删；域名失效时换同资源站的新域名即可
    {
        "name": "feifan",  # 非凡资源（樱空学园"线一"的上游）
        "api": "https://cj.ffzyapi.com/api.php/provide/vod",
    },
]
# 采集接口会对 httpx 默认 UA（python-httpx/…）返回 403，必须带浏览器风格 UA
VOD_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _pick_best_vod(rows: list[dict], term: str, year) -> dict:
    """采集接口按关键词搜可能返回多部（多季/相似名），打分挑最匹配的。"""
    tl = term.strip().lower()

    def score(v: dict) -> int:
        name = (v.get("vod_name") or "").strip().lower()
        s = 0
        if name == tl:
            s += 4
        elif tl in name or name in tl:
            s += 2
        else:
            s += 1  # 上游模糊搜索命中的弱匹配
        if year and str(v.get("vod_year") or "") == str(year):
            # 年份对上 → 大概率是正确的一季；多季番 AniList 按季分条目，
            # 季年份是比"名字完全相同"更强的区分信号，权重更高
            s += 3
        return s

    return max(rows, key=score)


def _parse_vod_play(vod: dict) -> list[dict]:
    """解析 vod_play_from/vod_play_url 为标准剧集列表。

    只保留 m3u8/mp4 直链（share 中转页、 magnet 等一律跳过）；
    多个播放组里优先选含直链的组。
    """
    froms = (vod.get("vod_play_from") or "").split("$$$")
    blocks = (vod.get("vod_play_url") or "").split("$$$")
    segments = list(zip(froms, blocks))

    def has_direct(block: str) -> bool:
        low = block.lower()
        return ".m3u8" in low or ".mp4" in low

    # 稳定排序：含直链的组在前，组间原顺序不变
    segments.sort(key=lambda fb: 0 if has_direct(fb[1]) else 1)

    for _from, block in segments:
        items = []
        for line in block.split("#"):
            if "$" not in line:
                continue
            ep_name, ep_url = line.split("$", 1)
            ep_url = ep_url.strip()
            low = ep_url.lower()
            if not low.startswith(("http://", "https://")):
                continue
            if not (".m3u8" in low or low.endswith(".mp4")):
                continue
            ep_name = ep_name.strip()
            items.append(
                {
                    "episode": extract_episode(ep_name),
                    "title": ep_name,
                    "url": ep_url,
                    "size": None,
                    "pubDate": "",
                    "_ts": 0.0,  # 采集结果本身有序，借 dedupe 统一格式
                }
            )
        if items:
            return dedupe_sort_episodes(items)
    return []


async def fetch_vod_source(
    client: httpx.AsyncClient, api: str, term: str, year
) -> list[dict]:
    """单个采集站的一次搜索；HTTP/JSON 错误由调用方捕获。"""
    resp = await client.get(api, params={"ac": "detail", "wd": term})
    resp.raise_for_status()
    rows = resp.json().get("list") or []
    if not rows:
        return []
    return _parse_vod_play(_pick_best_vod(rows, term, year))


async def fetch_vod_episodes_all(terms: list[str], year) -> tuple[list[dict], str]:
    """按 采集站×关键词 级联搜索，首个非空命中即返回 (episodes, 源名)。

    整体 12 秒预算；中文词要排在前面（这类站的目录以中文名为主）。
    """
    try:
        async with asyncio.timeout(12):
            for src in VOD_API_SOURCES:
                async with httpx.AsyncClient(
                    timeout=8.0, follow_redirects=True, headers=VOD_HEADERS
                ) as client:
                    for term in terms:
                        try:
                            eps = await fetch_vod_source(client, src["api"], term, year)
                        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
                            continue
                        if eps:
                            return eps, src["name"]
    except asyncio.TimeoutError:
        pass
    return [], ""


@app.get("/api/animes/{anilist_id}/videos")
async def anime_videos(anilist_id: int, kw: str = ""):
    """取某部番剧的在线播放剧集列表。

    数据源两类并联：MacCMS 采集站（m3u8，目录为简体，优先）+ ANi RSS（mp4，兜底）。
    上游失败/超时/未收录一律静默返回空 episodes（前端降级显示"暂无"）；
    仅 AniList 查无此番时返回 404（与 /resources 语义一致）。

    kw：搜索页带过来的用户关键词（常为简中番名，正是采集站搜索最需要的）。
    """
    hit = _video_cache.get(anilist_id)
    if hit and time.monotonic() - hit[0] < VIDEO_CACHE_TTL:
        return hit[1]

    try:
        data = await anilist_gql(ANILIST_MEDIA_Q, {"id": anilist_id})
    except httpx.HTTPError:
        return {"episodes": [], "source": ""}  # 拿不到标题候选，按无资源处理
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="未找到该番剧")

    title = media.get("title") or {}
    seen, terms = set(), []
    for t in (title.get("romaji"), title.get("native"), title.get("english")):
        if t and t not in seen:
            seen.add(t)
            terms.append(t)

    # 复用 titleZh 机制补一个中文候选词（简体对繁中库是 best-effort）：
    # 先查 _zh_cache（搜索页并发补全多半已填充，零成本）；
    # 没查过才花一次 AnimeGarden 请求推断并写回——空串也算查过，不反复重试。
    if anilist_id in _zh_cache:
        zh = _zh_cache[anilist_id] or None
    else:
        zh = None
        if terms:
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    zh = await asyncio.wait_for(
                        _fetch_zh_for_term(client, terms[0], terms), timeout=6.0
                    )
            except (httpx.HTTPError, asyncio.TimeoutError):
                zh = None
            _zh_cache[anilist_id] = zh or ""
    if zh and zh not in seen:
        seen.add(zh)
        terms.append(zh)

    # AniList synonyms（社区提交的别名）常含简体中文名——这正是采集站搜索
    # 需要的语种（其目录为简中，繁中/罗马音都搜不到）；滤掉含假名的日文别名，
    # 最多取 4 个控制上游请求数。
    zh_syns = []
    for s in media.get("synonyms") or []:
        if len(zh_syns) >= 4:
            break
        if s and _RE_CJK.search(s) and not _RE_KANA.search(s) and s not in seen:
            seen.add(s)
            zh_syns.append(s)

    # 两类源并联：MacCMS 采集站优先（曲库广），ANi 兜底；总预算 25 秒，
    # 采集站命中就取消 ANi 任务省上游压力。
    # 采集站候选词序：用户搜索词 > 简体同义词 > titleZh 推断 > 其余；
    # ANi 曲库是繁中/日文，维持罗马音优先，简中同义词追加在末尾。
    vod_terms = list(
        dict.fromkeys(([kw.strip()] if kw.strip() else []) + zh_syns + ([zh] if zh else []) + [t for t in terms if t != zh])
    )
    ani_terms = list(dict.fromkeys(terms + zh_syns))
    year = media.get("seasonYear")
    try:
        async with asyncio.timeout(25):
            vod_task = asyncio.create_task(fetch_vod_episodes_all(vod_terms, year))
            ani_task = asyncio.create_task(fetch_ani_episodes(ani_terms))
            episodes, source = await vod_task
            if episodes:
                ani_task.cancel()
            else:
                episodes, source = await ani_task
    except asyncio.TimeoutError:
        episodes, source = [], ""
    if not episodes:
        return {"episodes": [], "source": ""}
    payload = {"episodes": episodes, "source": source}
    _video_cache[anilist_id] = (time.monotonic(), payload)
    return payload


# ---------- 静态资源 ----------
# 头像文件托管（放在 / 之前，避免被前端 catch-all 拦截）
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# 托管前端静态页面：访问 http://localhost:8000 即可打开首页
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
