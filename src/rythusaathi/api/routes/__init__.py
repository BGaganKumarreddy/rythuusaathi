"""FastAPI route definitions for RythuSaathi."""

from .admin import router as admin_router
from .advisory import router as advisory_router
from .auth import router as auth_router
from .configuration import router as configuration_router
from .conversations import router as conversations_router
from .credentials import router as credentials_router
from .history import router as history_router

__all__ = [
    "admin_router",
    "advisory_router",
    "auth_router",
    "configuration_router",
    "conversations_router",
    "credentials_router",
    "history_router",
]

