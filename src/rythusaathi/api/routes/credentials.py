"""BYOK Credential vault routes: store, list, and delete encrypted provider keys."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from ...credentials import CredentialService
from ...security import VerifiedIdentity
from ..dependencies import get_credential_service, get_current_identity
from ..exceptions import APIError
from ..schemas.common import SuccessResponse
from ..schemas.credentials import CreateCredentialRequest, CredentialListResponse, CredentialResponse

router = APIRouter(prefix="/credentials", tags=["Credentials"])


@router.post(
    "",
    response_model=CredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Store an encrypted BYOK API key in the vault",
)
async def create_credential(
    req: CreateCredentialRequest,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    credential_service: Annotated[CredentialService, Depends(get_credential_service)],
) -> CredentialResponse:
    try:
        status_rec = credential_service.create(
            identity=identity,
            provider_id=req.provider_id,
            secret=req.secret,
        )
    except ValueError as exc:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_credential_input",
            message=str(exc),
        )

    return CredentialResponse(
        credential_id=status_rec.credential_id,
        provider_id=status_rec.provider_id,
        state=status_rec.state.value,
        generation=status_rec.generation,
        updated_at=status_rec.updated_at,
    )


@router.get(
    "",
    response_model=CredentialListResponse,
    summary="List stored BYOK credentials for the current user and tenant",
)
async def list_credentials(
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    credential_service: Annotated[CredentialService, Depends(get_credential_service)],
) -> CredentialListResponse:
    statuses = credential_service.list_status(identity)
    items = [
        CredentialResponse(
            credential_id=s.credential_id,
            provider_id=s.provider_id,
            state=s.state.value,
            generation=s.generation,
            updated_at=s.updated_at,
        )
        for s in statuses
    ]
    return CredentialListResponse(items=items, total=len(items))


@router.delete(
    "/{credential_id}",
    response_model=SuccessResponse,
    summary="Delete a stored credential from the encrypted vault",
)
async def delete_credential(
    credential_id: str,
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    credential_service: Annotated[CredentialService, Depends(get_credential_service)],
) -> SuccessResponse:
    credential_service.delete(identity, credential_id)
    return SuccessResponse(message=f"Credential '{credential_id}' successfully deleted")

