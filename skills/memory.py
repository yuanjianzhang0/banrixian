# -*- coding: utf-8 -*-
"""Memory skill for the planning agent.

This skill exposes long-term preferences, family preferences, session history,
and weather context in a structured way the agent can observe.
"""

from __future__ import annotations

from typing import Any


def get_memory(
    current_user: dict | None = None,
    family_members: list | None = None,
    preference_memory: list | None = None,
    session_history: list | None = None,
    weather_memory: dict | None = None,
) -> dict:
    current_user = current_user or {}
    family_members = family_members or []
    preference_memory = preference_memory or []
    session_history = session_history or []
    weather_memory = weather_memory or {}

    return {
        "type": "memory_snapshot",
        "current_user": {
            "id": str(current_user.get("id") or current_user.get("user_id") or ""),
            "name": str(current_user.get("name") or ""),
            "city": str(current_user.get("city") or ""),
        },
        "family_memory": [
            {
                "relation": str(item.get("relation") or ""),
                "avatar": str(item.get("avatar") or ""),
                "tags": item.get("tags") or [],
            }
            for item in family_members
            if isinstance(item, dict)
        ],
        "preference_memory": [
            {
                "memory_type": str(item.get("memory_type") or ""),
                "title": str(item.get("title") or ""),
                "content": str(item.get("content") or ""),
                "relation": str(item.get("relation") or ""),
                "source": str(item.get("source") or ""),
            }
            for item in preference_memory
            if isinstance(item, dict)
        ],
        "session_history": [
            {
                "role": str(item.get("role") or ""),
                "content": str(item.get("content") or ""),
            }
            for item in session_history
            if isinstance(item, dict)
        ],
        "weather_memory": {
            "city": str(weather_memory.get("city") or ""),
            "status": str(weather_memory.get("status") or "unknown"),
            "message": str(weather_memory.get("message") or ""),
        },
    }
