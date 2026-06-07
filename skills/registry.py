# -*- coding: utf-8 -*-
"""Registry for executable backend skills.

This first stage intentionally wraps the existing agent tools instead of moving
business logic. New skills such as weather and memory can be added here without
changing the chat endpoint contract.
"""

from __future__ import annotations

from typing import Any, Callable

from agent.tool_registry import TOOL_FUNCTIONS, get_tool_specs
from skills.memory import get_memory
from skills.weather import get_weather


EXTRA_SKILL_FUNCTIONS = {
    "get_memory": get_memory,
    "get_weather": get_weather,
}


EXTRA_SKILL_SPECS = [
    {
        "name": "get_memory",
        "description": "获取当前用户的长期偏好、亲友偏好、会话历史和天气上下文，帮助规划时参考记忆而不凭空猜测。",
        "arguments": {"current_user": {}, "family_members": [], "preference_memory": [], "session_history": [], "weather_memory": {}},
    },
    {
        "name": "get_weather",
        "description": "查询真实实时天气。只有 observation 返回 status=ok 时才能使用具体气温、降雨、风力等天气信息；status=unavailable 时禁止编造天气。",
        "arguments": {"city": "北京", "date": "today", "lat": None, "lng": None},
    }
]


def get_skill_specs() -> list[dict]:
    return [*get_tool_specs(), *EXTRA_SKILL_SPECS]


def get_skill_function(name: str) -> Callable[..., Any] | None:
    return TOOL_FUNCTIONS.get(name) or EXTRA_SKILL_FUNCTIONS.get(name)


def has_skill(name: str) -> bool:
    return name in TOOL_FUNCTIONS or name in EXTRA_SKILL_FUNCTIONS


def list_skill_names() -> list[str]:
    return [*TOOL_FUNCTIONS.keys(), *EXTRA_SKILL_FUNCTIONS.keys()]
