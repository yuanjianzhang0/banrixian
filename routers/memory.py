# -*- coding: utf-8 -*-
"""Agent memory routes."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, Depends

from agent.memory_store import _load_family_memory_for_user, _load_preference_memory_for_user
from core.auth import get_current_user
from core.database import get_db
from core.session import clear_session_history, get_session_history


logger = logging.getLogger("BanrixianAPI")
router = APIRouter(prefix="/v1/agent", tags=["agent", "memory"])


@router.get("/memory")
async def get_agent_memory(current_user: dict = Depends(get_current_user)):
    """Get agent memory snapshot."""
    return {
        "code": 200,
        "data": {
            "familyMemory": _load_family_memory_for_user(current_user),
            "preferenceMemory": _load_preference_memory_for_user(current_user),
            "sessionHistory": get_session_history(current_user),
        },
    }


@router.post("/memory/session/clear")
async def clear_agent_session_memory(current_user: dict = Depends(get_current_user)):
    """Clear session memory."""
    clear_session_history(current_user)
    return {"code": 200, "message": "session cleared"}


# ── 聊天记录云同步 ──────────────────────────────────────────────────────────────

@router.get("/chat/sync")
async def get_chat_sync(current_user: dict = Depends(get_current_user)):
    """拉取该用户的聊天记录（最多 60 条消息 + 最后路线）。"""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT messages, last_plan FROM user_chat_sync WHERE user_id = %s",
                (current_user["id"],),
            )
            row = cursor.fetchone()
        if not row:
            return {"code": 200, "data": {"messages": [], "lastPlan": None}}
        messages = json.loads(row["messages"] or "[]")
        last_plan = json.loads(row["last_plan"]) if row.get("last_plan") else None
        return {"code": 200, "data": {"messages": messages, "lastPlan": last_plan}}
    except Exception as exc:
        logger.error("拉取聊天同步失败 user=%s: %s", current_user.get("name"), exc)
        return {"code": 200, "data": {"messages": [], "lastPlan": None}}
    finally:
        conn.close()


@router.post("/chat/sync")
async def post_chat_sync(
    payload: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """上传该用户的聊天记录（最近 60 条 + 最后路线）。"""
    messages = payload.get("messages") or []
    last_plan = payload.get("lastPlan")
    if not isinstance(messages, list):
        messages = []
    messages = messages[-60:]  # 最多保存 60 条
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO user_chat_sync (user_id, messages, last_plan)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                     messages = VALUES(messages),
                     last_plan = VALUES(last_plan),
                     updated_at = NOW()""",
                (
                    current_user["id"],
                    json.dumps(messages, ensure_ascii=False),
                    json.dumps(last_plan, ensure_ascii=False) if last_plan else None,
                ),
            )
        conn.commit()
        return {"code": 200, "message": "sync ok"}
    except Exception as exc:
        logger.error("上传聊天同步失败 user=%s: %s", current_user.get("name"), exc)
        return {"code": 500, "message": "sync failed"}
    finally:
        conn.close()
