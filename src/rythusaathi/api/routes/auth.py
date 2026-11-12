"""Authentication routes: user registration, session login, logout, and identity profile."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, status

from ...local_auth import LocalAuthentication, _db
from ...security import IdentityRole, VerifiedIdentity
from ..dependencies import extract_bearer_token, get_auth_service, get_current_identity
from ..exceptions import APIError
from ..schemas.auth import LoginRequest, LoginResponse, RegisterRequest, RegisterResponse, UserResponse
from ..schemas.common import SuccessResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    req: RegisterRequest,
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
) -> RegisterResponse:
    # Check tenant existence to prevent privilege escalation into existing tenants
    tenant_exists = False
    with _db(auth_service.database) as db:
        row = db.execute("SELECT 1 FROM tenants WHERE tenant_id = ?", (req.tenant_id,)).fetchone()
        tenant_exists = row is not None

    if tenant_exists:
        if req.role == IdentityRole.TENANT_ADMIN:
            raise APIError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="privilege_escalation_denied",
                message=f"Cannot register as tenant_admin for existing tenant '{req.tenant_id}' without prior administrative authorization",
            )
    else:
        try:
            auth_service.create_tenant(req.tenant_id, req.tenant_name or req.tenant_id)
        except Exception as exc:
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="tenant_creation_failed",
                message=f"Failed to create tenant '{req.tenant_id}': {str(exc)}",
            )

    try:
        user_id = auth_service.register(
            username=req.username,
            password=req.password,
            tenant_id=req.tenant_id,
            role=req.role,
        )
    except sqlite3.IntegrityError:
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="username_already_registered",
            message=f"Username '{req.username}' is already registered in the system",
        )
    except ValueError as exc:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_registration",
            message=str(exc),
        )

    return RegisterResponse(
        user_id=user_id,
        username=req.username,
        tenant_id=req.tenant_id,
        role=req.role.value,
        message="User account successfully registered",
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate user and issue session token",
)
async def login(
    req: LoginRequest,
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
) -> LoginResponse:
    try:
        token = auth_service.login(req.username, req.password, req.tenant_id)
        identity = auth_service.authenticate(token)
    except PermissionError:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="authentication_failed",
            message="Invalid username, password, or tenant ID",
        )

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_at=identity.expires_at,
        user_id=identity.subject,
        tenant_id=identity.tenant_id,
        roles=[r.value for r in identity.roles],
    )


@router.post(
    "/logout",
    response_model=SuccessResponse,
    summary="Invalidate current session token",
)
async def logout(
    token: Annotated[str, Depends(extract_bearer_token)],
    auth_service: Annotated[LocalAuthentication, Depends(get_auth_service)],
) -> SuccessResponse:
    auth_service.logout(token)
    return SuccessResponse(message="Session successfully terminated")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Retrieve current user profile and tenant identity",
)
async def me(
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
) -> UserResponse:
    return UserResponse(
        user_id=identity.subject,
        tenant_id=identity.tenant_id,
        roles=[r.value for r in identity.roles],
        expires_at=identity.expires_at,
        issuer=identity.issuer,
    )

