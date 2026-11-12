"""Shared Pydantic models and response wrappers for RythuSaathi API."""

from __future__ import annotations

from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class APIErrorDetail(BaseModel):
    """Safe, structured error detail; never leaks stack traces or credentials."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable explanation")
    details: dict[str, str] | None = Field(default=None, description="Optional safe contextual details")


class SuccessResponse(BaseModel):
    """Generic status response for mutation operations."""

    model_config = ConfigDict(frozen=True)

    status: str = "success"
    message: str

