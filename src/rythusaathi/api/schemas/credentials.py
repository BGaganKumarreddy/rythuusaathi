"""BYOK Credential request and response schemas."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class CreateCredentialRequest(BaseModel):
    """Payload to store an encrypted BYOK credential in the vault."""

    model_config = ConfigDict(frozen=True)

    provider_id: str = Field(min_length=1, max_length=64, description="Target provider (e.g. cerebras, gemini, groq, data.gov.in)")
    secret: str = Field(min_length=1, max_length=512, description="API key or secret token")


class CredentialResponse(BaseModel):
    """Non-secret status of a stored credential; the key is never returned."""

    model_config = ConfigDict(frozen=True)

    credential_id: str
    provider_id: str
    state: str
    generation: int
    updated_at: datetime


class CredentialListResponse(BaseModel):
    """List of active credentials for the authenticated tenant."""

    model_config = ConfigDict(frozen=True)

    items: list[CredentialResponse]
    total: int

