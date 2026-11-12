"""Authentication request and response schemas."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from ...security import IdentityRole


class RegisterRequest(BaseModel):
    """New user registration payload."""

    model_config = ConfigDict(frozen=True)

    username: str = Field(min_length=3, max_length=64, description="Trimmed unique username")
    password: str = Field(min_length=12, max_length=128, description="Password with minimum 12 characters")
    tenant_id: str = Field(default="default", min_length=1, max_length=64, description="Target tenant identifier")
    tenant_name: str | None = Field(default=None, description="Optional tenant name when registering a new tenant")
    role: IdentityRole = Field(default=IdentityRole.MEMBER, description="Assigned role within the tenant")


class RegisterResponse(BaseModel):
    """User registration confirmation."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    username: str
    tenant_id: str
    role: str
    message: str = "User successfully registered"


class LoginRequest(BaseModel):
    """User login credential payload."""

    model_config = ConfigDict(frozen=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default="default", min_length=1, max_length=64)


class LoginResponse(BaseModel):
    """Session authentication token response."""

    model_config = ConfigDict(frozen=True)

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user_id: str
    tenant_id: str
    roles: list[str]


class UserResponse(BaseModel):
    """Current authenticated user profile."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_id: str
    roles: list[str]
    expires_at: datetime
    issuer: str

