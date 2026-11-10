"""Backend inference contracts and providers."""

from .contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceProvider,
    InferenceStage,
    InferenceTokenUsage,
    RateLimitScope,
    RateLimitSnapshot,
    ProviderCredential,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResponse,
)

from .service import (
    AuthenticatedGeminiService,
    AuthenticatedGroqService,
    AuthenticatedIdentityMismatch,
    AuthenticatedInferenceService,
)

__all__ = [
    "AuthenticatedGeminiService",
    "AuthenticatedGroqService",
    "AuthenticatedIdentityMismatch",
    "AuthenticatedInferenceService",
    "InferenceDiagnostic",
    "InferenceDiagnosticCode",
    "InferenceRequest",
    "InferenceProvider",
    "InferenceStage",
    "InferenceTokenUsage",
    "RateLimitScope",
    "RateLimitSnapshot",
    "ProviderCredential",
    "ProviderFailure",
    "ProviderFailureKind",
    "ProviderResponse",
]
