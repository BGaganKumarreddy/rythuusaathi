"""Standardized HTTP exceptions and exception handlers for RythuSaathi API."""

from __future__ import annotations

import logging
from typing import Any
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..credentials import (
    CredentialRecoveryRequired,
    CredentialUnavailable,
    CredentialValidationFailed,
    OsCredentialStoreUnavailable,
)
from ..security import AuthorizationDenied

logger = logging.getLogger(__name__)


class APIError(HTTPException):
    """Custom API error carrying machine-readable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message, "details": details or {}},
        )


def register_exception_handlers(app: FastAPI) -> None:
    """Register uniform, safe exception handlers that prevent diagnostic leaks."""

    @app.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    @app.exception_handler(AuthorizationDenied)
    async def auth_denied_handler(_request: Request, _exc: AuthorizationDenied) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "code": "forbidden",
                "message": "Operation is not authorized for the current identity",
                "details": {},
            },
        )

    @app.exception_handler(CredentialUnavailable)
    async def cred_unavailable_handler(_request: Request, _exc: CredentialUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "code": "credential_unavailable",
                "message": "Requested credential was not found or is unavailable",
                "details": {},
            },
        )

    @app.exception_handler(CredentialValidationFailed)
    async def cred_validation_handler(_request: Request, _exc: CredentialValidationFailed) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": "credential_validation_failed",
                "message": "Supplied credential secret failed validation check",
                "details": {},
            },
        )

    @app.exception_handler(CredentialRecoveryRequired)
    async def cred_recovery_handler(_request: Request, _exc: CredentialRecoveryRequired) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "code": "vault_recovery_required",
                "message": "Credential vault is locked in recovery state",
                "details": {},
            },
        )

    @app.exception_handler(OsCredentialStoreUnavailable)
    async def os_store_unavailable_handler(_request: Request, _exc: OsCredentialStoreUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "code": "os_key_vault_unavailable",
                "message": "Underlying OS key encryption vault is currently unavailable",
                "details": {},
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": " -> ".join(str(loc) for loc in err["loc"]), "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            content={
                "code": "validation_error",
                "message": "Request payload validation failed",
                "details": {"errors": errors},
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server exception: %s", type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": "internal_server_error",
                "message": "An internal error occurred while processing the request",
                "details": {},
            },
        )
