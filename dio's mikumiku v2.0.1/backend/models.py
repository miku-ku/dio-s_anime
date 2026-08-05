"""Pydantic 请求/响应模型。"""
from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """注册请求体。"""
    username: str = Field(
        min_length=3, max_length=20,
        pattern=r"^[A-Za-z0-9_一-龥]+$",
        description="3-20 位字母、数字、下划线或中文",
    )
    password: str = Field(min_length=6, max_length=50, description="至少 6 位")
    remember: bool = False  # 记住我：注册返回的令牌有效期也延长到 30 天


class LoginRequest(BaseModel):
    """登录请求体。"""
    username: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=50)
    remember: bool = False  # 记住我：令牌有效期延长到 30 天


class ChangePasswordRequest(BaseModel):
    """修改密码请求体。"""
    old_password: str = Field(min_length=1, max_length=50)
    new_password: str = Field(min_length=6, max_length=50, description="至少 6 位")


class BioUpdateRequest(BaseModel):
    """修改个人简介请求体。"""
    bio: str = Field(default="", max_length=200, description="不超过 200 字")


class UserOut(BaseModel):
    """返回给前端的用户信息（不含密码）。"""
    id: int
    username: str
    avatar: str | None = None  # 头像 URL，未上传时为 null
    bio: str | None = None     # 个人简介
    created_at: str | None = None


class TokenOut(BaseModel):
    """登录/注册成功后的响应。"""
    token: str
    user: UserOut
