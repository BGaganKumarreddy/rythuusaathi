"""Authenticated backend inference boundary for stored provider credentials."""

from __future__ import annotations

from .contracts import (InferenceProvider, InferenceRequest, ProviderCredential,
                        ProviderFailure, ProviderFailureKind, ProviderResponse)
from ..credentials import CredentialService
from ..local_auth import LocalAuthentication


class AuthenticatedIdentityMismatch(PermissionError):
    """Caller-supplied workflow identity differs from the verified session."""


class AuthenticatedInferenceService:
    """Authenticate a local session, authorize its BYOK record, then call the provider.

    The decrypted string is converted immediately to the existing one-use
    `ProviderCredential`. It is held only in the provider-call coroutine and is
    never retained as a service field, returned to the caller, or logged here.
    """

    def __init__(
        self,
        authentication: LocalAuthentication,
        credential_service: CredentialService,
        provider: InferenceProvider,
        *,
        expected_provider_id: str | None = None,
    ):
        provider_id = getattr(provider, "provider_id", None)
        if expected_provider_id is not None:
            if provider_id != expected_provider_id:
                raise ValueError(
                    f"Service requires provider '{expected_provider_id}', got '{provider_id}'"
                )
            self._expected_provider_id = expected_provider_id
        else:
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError("InferenceProvider must specify a non-empty provider_id")
            self._expected_provider_id = provider_id
        self._authentication = authentication
        self._credentials = credential_service
        self._provider = provider

    @property
    def provider_id(self) -> str:
        return self._expected_provider_id

    @property
    def model_id(self) -> str | None:
        """Expose non-secret model metadata for execution observability."""
        config = getattr(self._provider, "config", None)
        value = getattr(config, "model_id", None)
        return value if isinstance(value, str) and value else None

    async def complete(self, session_token: str, credential_id: str,
                       request: InferenceRequest, *,
                       expected_user_id: str | None = None,
                       expected_tenant_id: str | None = None) -> ProviderResponse:
        identity = self._authentication.authenticate(session_token)
        # Workflow request fields are untrusted. When an application binds an
        # advisory to an identity, compare those fields with the independently
        # authenticated session before selecting an owner-scoped credential.
        if ((expected_user_id is not None and identity.subject != expected_user_id)
                or (expected_tenant_id is not None and identity.tenant_id != expected_tenant_id)):
            raise AuthenticatedIdentityMismatch(
                "Authenticated identity does not match the advisory request"
            ) from None
        provider_call = self._credentials.dispatch_handoff(
            identity,
            credential_id,
            lambda value, _generation: self._provider.complete(
                request, ProviderCredential(value)
            ),
            expected_provider_id=self._expected_provider_id,
        )
        failure: ProviderFailure | None = None
        result: ProviderResponse | None = None
        try:
            result = await provider_call
        except ProviderFailure as exc:
            failure = ProviderFailure(exc.kind, exc.diagnostic)
        except Exception:
            # A provider wrapper must not let arbitrary SDK exception text escape.
            failure = ProviderFailure(ProviderFailureKind.PROVIDER_FAILURE)
        if failure is not None:
            # Raise after leaving the handler so SDK errors are not retained as context.
            raise failure from None
        if not isinstance(result, ProviderResponse):
            raise ProviderFailure(ProviderFailureKind.PROVIDER_FAILURE) from None
        return result


class AuthenticatedGroqService(AuthenticatedInferenceService):
    """Authenticated inference service specialized for Groq."""

    def __init__(
        self,
        authentication: LocalAuthentication,
        credential_service: CredentialService,
        provider: InferenceProvider,
    ):
        if getattr(provider, "provider_id", None) != "groq":
            raise ValueError("AuthenticatedGroqService requires the Groq provider")
        super().__init__(
            authentication,
            credential_service,
            provider,
            expected_provider_id="groq",
        )


class AuthenticatedGeminiService(AuthenticatedInferenceService):
    """Authenticated inference service specialized for Gemini."""

    def __init__(
        self,
        authentication: LocalAuthentication,
        credential_service: CredentialService,
        provider: InferenceProvider,
    ):
        if getattr(provider, "provider_id", None) != "gemini":
            raise ValueError("AuthenticatedGeminiService requires the Gemini provider")
        super().__init__(
            authentication,
            credential_service,
            provider,
            expected_provider_id="gemini",
        )


