"""Pydantic schemas for the RythuSaathi REST API."""

from .common import APIErrorDetail, SuccessResponse
from .auth import LoginRequest, LoginResponse, RegisterRequest, RegisterResponse, UserResponse
from .credentials import CreateCredentialRequest, CredentialListResponse, CredentialResponse
from .advisory import (
    AdvisoryCreateRequest,
    AdvisoryResponseSchema,
    AdvisoryTraceResponse,
    AgentResultSchema,
    EvidenceItemSchema,
    RiskAlertSchema,
)
from .configuration import CropSchema, LocationSchema, ProviderSchema
from .history import HistoryItemSchema, HistoryListResponse

__all__ = [
    "APIErrorDetail",
    "AdvisoryCreateRequest",
    "AdvisoryResponseSchema",
    "AdvisoryTraceResponse",
    "AgentResultSchema",
    "CreateCredentialRequest",
    "CredentialListResponse",
    "CredentialResponse",
    "CropSchema",
    "EvidenceItemSchema",
    "HistoryItemSchema",
    "HistoryListResponse",
    "LocationSchema",
    "LoginRequest",
    "LoginResponse",
    "ProviderSchema",
    "RegisterRequest",
    "RegisterResponse",
    "RiskAlertSchema",
    "SuccessResponse",
    "UserResponse",
]

