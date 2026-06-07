# -*- coding: utf-8 -*-
"""Stable SSE event helpers for the chat and agent pipeline."""

from __future__ import annotations

import json
from typing import Any


VALID_EVENT_TYPES = {"status", "intent", "skill_call", "observation", "done", "error"}


def make_event(event_type: str, **payload: Any) -> dict:
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"Unsupported SSE event type: {event_type}")
    return {"type": event_type, **payload}


def status_event(content: str, **extra: Any) -> dict:
    return make_event("status", content=content, **extra)


def intent_event(intent: str, confidence: Any = None, reason: str | None = None, **extra: Any) -> dict:
    return make_event("intent", intent=intent, confidence=confidence, reason=reason, **extra)


def skill_call_event(
    skill: str,
    arguments: dict | None = None,
    visible_reason: str | None = None,
    step: int | None = None,
    **extra: Any,
) -> dict:
    event = make_event(
        "skill_call",
        skill=skill,
        tool=skill,
        arguments=arguments or {},
        visible_reason=visible_reason or "",
        **extra,
    )
    if step is not None:
        event["step"] = step
    return event


def observation_event(
    skill: str,
    summary: str = "",
    data: dict | None = None,
    step: int | None = None,
    **extra: Any,
) -> dict:
    event = make_event(
        "observation",
        skill=skill,
        tool=skill,
        summary=summary,
        data=data or {},
        **extra,
    )
    if step is not None:
        event["step"] = step
    return event


def done_event(data: Any) -> dict:
    return make_event("done", data=data)


def error_event(content: str, code: str = "AI_ENGINE_ERROR", **extra: Any) -> dict:
    return make_event("error", content=content, code=code, **extra)


def sse_format(event: dict) -> str:
    """Serialize one event for text/event-stream responses."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

