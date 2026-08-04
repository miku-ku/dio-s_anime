"""密码哈希与 JWT 令牌的生成/校验。"""
import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

import jwt  # PyJWT

# 生产环境请通过环境变量 SECRET_KEY 注入一个随机长字符串
_DEFAULT_SECRET = "dev-secret-key-please-change"
SECRET_KEY = os.environ.get("SECRET_KEY", _DEFAULT_SECRET)
if SECRET_KEY == _DEFAULT_SECRET:
    # Python logging 的 lastResort handler 会把 WARNING+ 打到 stderr，uvicorn 下可见
    logging.warning(
        "⚠️ 未设置 SECRET_KEY 环境变量，正使用内置默认密钥——仅限本地开发！"
        "生产环境请设置一个随机长字符串，否则任何人都能伪造本站登录令牌。"
    )
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24          # 默认登录令牌有效期
TOKEN_EXPIRE_HOURS_REMEMBER = 24 * 30  # “记住我”时的有效期（30 天）
PBKDF2_ITERATIONS = 200_000      # 哈希迭代次数


# ---------- 密码哈希 ----------

def hash_password(password: str) -> str:
    """用 PBKDF2-SHA256 + 随机盐 哈希密码，返回 '盐hex$哈希hex'。"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文密码与数据库中的哈希是否匹配。"""
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), dk_hex)


# ---------- JWT ----------

def create_token(user_id: int, username: str, remember: bool = False, tv: int = 0) -> str:
    """签发 JWT，携带用户 id、用户名、令牌版本号和过期时间。

    remember=True 时有效期 30 天；tv 是用户的 token_version，
    用户改密后版本号 +1，旧令牌随之失效（有限吊销）。
    """
    hours = TOKEN_EXPIRE_HOURS_REMEMBER if remember else TOKEN_EXPIRE_HOURS
    expire = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {"sub": str(user_id), "username": username, "exp": expire, "tv": tv}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """校验并解析 JWT；失败时抛出 jwt.PyJWTError。"""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
