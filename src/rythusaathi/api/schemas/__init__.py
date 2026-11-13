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
from .conversations import (
    ContextStateSchema,
    ConversationDetailSchema,
    ConversationListResponse,
    ConversationMessageSchema,
    ConversationSummarySchema,
    CreateConversationRequest,
    SendMessageRequest,
)

__all__ = [
    "APIErrorDetail",
    "AdvisoryCreateRequest",
    "AdvisoryResponseSchema",
    "AdvisoryTraceResponse",
    "AgentResultSchema",
    "ContextStateSchema",
    "ConversationDetailSchema",
    "ConversationListResponse",
    "ConversationMessageSchema",
    "ConversationSummarySchema",
    "CreateConversationRequest",
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
    "SendMessageRequest",
    "SuccessResponse",
    "UserResponse",
]

