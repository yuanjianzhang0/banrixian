# -*- coding: utf-8 -*-
"""Short-lived in-memory chat session history."""

from __future__ import annotations

from core.config import MAX_CHAT_HISTORY_ITEMS


CHAT_HISTORY: dict[str, list[dict]] = {}


def user_id(current_user: dict) -> str:
    return str(current_user.get("id") or current_user.get("user_id") or current_user.get("openid") or "anonymous")


def get_session_history(current_user: dict) -> list:
    return list(CHAT_HISTORY.get(user_id(current_user), []))


def append_session_history(current_user: dict, user_text: str, assistant_summary: str) -> None:
    uid = user_id(current_user)
    history = list(CHAT_HISTORY.get(uid, []))
    if user_text:
        history.append({"role": "user", "content": user_text})
    if assistant_summary:
        history.append({"role": "assistant", "content": assistant_summary})
    CHAT_HISTORY[uid] = history[-MAX_CHAT_HISTORY_ITEMS:]


def clear_session_history(current_user: dict) -> None:
    CHAT_HISTORY[user_id(current_user)] = []


# Backward-compatible aliases for migrated route helpers.
_user_id = user_id
_get_session_history = get_session_history
_append_session_history = append_session_history
