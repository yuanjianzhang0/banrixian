# -*- coding: utf-8 -*-
"""API routers for Banrixian backend."""

from __future__ import annotations

from .auth import router as auth_router
from .user import router as user_router
from .map import router as map_router
from .orders import router as orders_router
from .memory import router as memory_router
from .notifications import router as notifications_router
from .chat import router as chat_router
from .share import router as share_router


__all__ = [
    "auth_router",
    "user_router",
    "map_router",
    "orders_router",
    "memory_router",
    "notifications_router",
    "chat_router",
    "share_router",
]
