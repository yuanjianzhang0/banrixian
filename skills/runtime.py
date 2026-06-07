# -*- coding: utf-8 -*-
"""Unified runtime for executing backend skills."""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any

from agent.tool_registry import TOOL_FUNCTIONS, ToolContext, execute_tool
from skills.registry import get_skill_function


def _weather_guard_timeout_seconds() -> float:
    raw = os.getenv("WEATHER_TIMEOUT_SECONDS", "3").strip()
    try:
        return max(1.0, min(5.5, float(raw) + 0.5))
    except ValueError:
        return 3.5


def _weather_unavailable(city: str, reason: str) -> dict:
    return {
        "type": "weather",
        "status": "unavailable",
        "city": city or "北京",
        "source": "none",
        "message": f"实时天气暂不可用：{reason}",
        "advice": "不要编造气温、降雨、风力等实时天气信息；可基于用户偏好给出保守室内/室外备选。",
    }


class SkillRuntime:
    """Execute registered skills and return normalized observations.

    The current implementation delegates to the existing agent tool registry.
    That keeps this migration small while giving the backend one place to add
    weather, memory, and other future capabilities.
    """

    def __init__(self, context: ToolContext):
        self.context = context

    async def execute(self, skill_name: str, arguments: dict | None = None) -> dict:
        arguments = dict(arguments or {})
        if skill_name in TOOL_FUNCTIONS:
            return await self._execute_legacy_tool(skill_name, arguments)

        return await self._execute_extra_skill(skill_name, arguments)

    async def _execute_legacy_tool(self, skill_name: str, arguments: dict) -> dict:
        try:
            observation = await execute_tool(skill_name, arguments, self.context)
        except Exception as exc:
            return {
                "ok": False,
                "tool_name": skill_name,
                "skill_name": skill_name,
                "error": f"{skill_name} failed: {exc}",
            }

        if isinstance(observation, dict):
            observation.setdefault("skill_name", observation.get("tool_name") or skill_name)
            return observation

        return {
            "ok": True,
            "tool_name": skill_name,
            "skill_name": skill_name,
            "result": observation,
        }

    async def _execute_extra_skill(self, skill_name: str, arguments: dict) -> dict:
        skill_func = get_skill_function(skill_name)
        if not skill_func:
            return {"ok": False, "tool_name": skill_name, "skill_name": skill_name, "error": f"Unknown skill: {skill_name}"}

        if skill_name == "get_weather":
            arguments.setdefault("current_user", self.context.current_user)
            if self.context.profile:
                arguments.setdefault("city", self.context.profile.get("city"))
        elif skill_name == "get_memory":
            arguments.setdefault("current_user", self.context.current_user)
            arguments.setdefault("family_members", self.context.family_members)
            arguments.setdefault("preference_memory", self.context.preference_memory or [])
            arguments.setdefault("session_history", self.context.session_history or [])
            arguments.setdefault("weather_memory", self.context.weather or {})

        try:
            result = skill_func(**arguments)
            if inspect.isawaitable(result):
                if skill_name == "get_weather":
                    result = await asyncio.wait_for(result, timeout=_weather_guard_timeout_seconds())
                else:
                    result = await result
        except asyncio.TimeoutError:
            if skill_name == "get_weather":
                city = str(arguments.get("city") or (self.context.current_user or {}).get("city") or "北京")
                result = _weather_unavailable(city, "天气服务超过预算未返回")
                self.context.weather = result
                self.context.observations.append({"tool_name": skill_name, "skill_name": skill_name, "result": result})
                return {"ok": True, "tool_name": skill_name, "skill_name": skill_name, "result": result}
            return {
                "ok": False,
                "tool_name": skill_name,
                "skill_name": skill_name,
                "error": f"{skill_name} timeout",
            }
        except Exception as exc:
            return {
                "ok": False,
                "tool_name": skill_name,
                "skill_name": skill_name,
                "error": f"{skill_name} failed: {exc}",
            }

        if skill_name == "get_weather" and isinstance(result, dict):
            self.context.weather = result

        self.context.observations.append({"tool_name": skill_name, "skill_name": skill_name, "result": result})
        return {"ok": True, "tool_name": skill_name, "skill_name": skill_name, "result": result}

    async def __call__(self, skill_name: str, arguments: dict | None = None) -> dict:
        return await self.execute(skill_name, arguments)
