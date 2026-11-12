"""History route for paginated advisory consultations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ...security import VerifiedIdentity
from ..dependencies import get_current_identity, get_history_store
from ..history import AdvisoryHistoryStore
from ..schemas.history import HistoryListResponse

router = APIRouter(prefix="/history", tags=["History"])


@router.get(
    "",
    response_model=HistoryListResponse,
    summary="Retrieve paginated advisory consultation history for the authenticated user and tenant",
)
async def get_history(
    identity: Annotated[VerifiedIdentity, Depends(get_current_identity)],
    history_store: Annotated[AdvisoryHistoryStore, Depends(get_history_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    crop: Annotated[str | None, Query(description="Filter history by crop identifier or common name")] = None,
) -> HistoryListResponse:
    items, total = history_store.list(identity, limit=limit, offset=offset, crop=crop)
    return HistoryListResponse(items=items, total=total, limit=limit, offset=offset)

