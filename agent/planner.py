# -*- coding: utf-8 -*-
"""Agent main loop: LLM decision -> Python skill -> observation -> final plan."""

from __future__ import annotations

import copy
import json
from typing import AsyncIterator

from core.sse import done_event, observation_event, skill_call_event, status_event
from skills.runtime import SkillRuntime

from .harness import AgentHarness, HarnessOptions
from .llm_client import LLMUnavailable, create_llm_client
from .prompts import build_agent_system_prompt, build_user_message
from .schemas import dumps, normalize_plan, parse_json_object
from .tool_registry import ToolContext
from .tools import build_final_plan, valid_place_names


MAX_STEPS = 10
PUBLIC_OBSERVATION_LIMIT = 5


def parse_llm_decision(raw_text: str) -> dict:
    decision = parse_json_object(raw_text)
    if not isinstance(decision, dict):
        return {"type": "invalid", "raw": raw_text}
    if decision.get("type") == "tool_call":
        return {
            "type": "tool_call",
            "tool": decision.get("tool"),
            "arguments": decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {},
            "visible_reason": decision.get("visible_reason") or "需要调用工具获取下一步信息",
        }
    if decision.get("type") == "final_answer":
        return {"type": "final_answer", "data": decision.get("data") or {}}
    return {"type": "invalid", "raw": raw_text}


def _observation_message(observation: dict) -> dict[str, str]:
    return {
        "role": "user",
        "content": "OBSERVATION_JSON:\n" + json.dumps(observation, ensure_ascii=False),
    }


async def _finish_from_context(context: ToolContext) -> dict:
    plan = build_final_plan(
        profile=context.profile or {},
        ranked_places=context.ranked_places,
        actions=context.actions,
        observations=context.observations,
        current_time=context.current_time,
        goal=context.goal,
        time_plan=context.time_plan,
        risks=context.risks,
        original_text=context.text,
    )
    return normalize_plan(plan, fallback_profile=context.profile or {})


def _plan_uses_known_places(plan: dict, places: list | None) -> bool:
    names = valid_place_names(places)
    steps = plan.get("steps") or []
    if not steps or not names:
        return False
    return all(isinstance(step, dict) and step.get("name") in names for step in steps)


def _actions_are_usable(plan: dict, places: list | None) -> bool:
    actions = plan.get("actions") or []
    if not actions:
        return True  # 用户未要求预约时 actions 为空是合法的

    valid_names = valid_place_names(places)
    for action in actions:
        if not isinstance(action, dict) or action.get("type") != "reserve":
            continue
        target = action.get("target")
        if target and valid_names and target not in valid_names:
            return False
    return True


def _plan_is_usable(plan: dict, context: ToolContext) -> bool:
    return bool(
        isinstance(plan, dict)
        and plan.get("type") == "plan"
        and plan.get("steps")
        and _plan_uses_known_places(plan, context.places)
        and _actions_are_usable(plan, context.places)
    )


def _compact_place(place: object) -> object:
    if not isinstance(place, dict):
        return place
    keep = (
        "name",
        "category",
        "keyword",
        "address",
        "open_hours",
        "price_range",
        "score",
        "final_score",
        "score_breakdown",
        "risks",
        "reason",
        "lng",
        "lat",
    )
    return {key: place.get(key) for key in keep if key in place}


def _observation_summary(skill_name: str, observation: dict) -> str:
    result = observation.get("result") if isinstance(observation, dict) else {}
    if not isinstance(result, dict):
        return "工具已返回结果" if observation.get("ok") else "工具调用失败"

    if skill_name == "get_current_time":
        return result.get("readable") or "已获取服务器当前时间"
    if skill_name == "decompose_goal":
        slots = "、".join(result.get("task_slots") or [])
        return f"已拆解用户目标{f'：{slots}' if slots else ''}"
    if skill_name == "plan_time_slots":
        warning = "；".join(result.get("warnings") or [])
        return warning or f"已生成不会落在过去的时间安排：{result.get('start')} - {result.get('end')}"
    if skill_name == "analyze_user_profile":
        scenes = "、".join(result.get("scene") or [])
        return f"已完成用户画像分析{f'：{scenes}' if scenes else ''}"
    if skill_name == "search_places":
        count = result.get("count")
        if count is None:
            count = len(result.get("places") or [])
        return f"已找到 {count} 个候选地点"
    if skill_name == "rank_places_for_plan":
        return f"已完成排序，保留 {len(result.get('ranked_places') or [])} 个候选地点"
    if skill_name == "get_weather":
        if result.get("status") == "ok":
            return result.get("message") or "已获取实时天气"
        return result.get("message") or "实时天气暂不可用"
    if skill_name == "mock_reserve":
        return result.get("message") or "已完成模拟预约"
    if skill_name == "mock_create_order":
        return result.get("message") or "已生成模拟订单"
    if skill_name == "build_final_plan":
        return f"已整理最终方案，包含 {len(result.get('steps') or [])} 个步骤"
    return "工具已返回结果" if observation.get("ok") else "工具调用失败"


def _public_observation(skill_name: str, observation: dict) -> dict:
    public_data = copy.deepcopy(observation)
    result = public_data.get("result") if isinstance(public_data, dict) else None
    if isinstance(result, dict):
        if isinstance(result.get("places"), list):
            result["count"] = result.get("count", len(result["places"]))
            result["places"] = [_compact_place(place) for place in result["places"][:PUBLIC_OBSERVATION_LIMIT]]
        if isinstance(result.get("ranked_places"), list):
            result["count"] = result.get("count", len(result["ranked_places"]))
            result["ranked_places"] = [
                _compact_place(place) for place in result["ranked_places"][:PUBLIC_OBSERVATION_LIMIT]
            ]
    return observation_event(
        skill=skill_name,
        summary=_observation_summary(skill_name, observation),
        data=public_data,
    )


async def _run_agent_events(
    text: str,
    current_user: dict | None = None,
    family_members: list | None = None,
    places: list | None = None,
    history: list | None = None,
    preference_memory: list | None = None,
    session_history: list | None = None,
    weather_memory: dict | None = None,
    max_steps: int = MAX_STEPS,
) -> AsyncIterator[dict]:
    harness = AgentHarness(
        text=text or "",
        current_user=current_user,
        family_members=family_members,
        places=places,
        history=history,
        preference_memory=preference_memory,
        session_history=session_history,
        weather_memory=weather_memory,
        options=HarnessOptions(max_steps=max_steps, guided_bootstrap=True),
    )
    async for event in harness.run():
        yield event


async def run_agent(
    text: str,
    current_user: dict | None = None,
    family_members: list | None = None,
    places: list | None = None,
    history: list | None = None,
    preference_memory: list | None = None,
    session_history: list | None = None,
    weather_memory: dict | None = None,
) -> dict:
    final_data: dict | None = None
    async for event in _run_agent_events(
        text,
        current_user,
        family_members,
        places,
        history=history,
        preference_memory=preference_memory,
        session_history=session_history,
        weather_memory=weather_memory,
    ):
        if event.get("type") == "done":
            final_data = event.get("data")
    return final_data or normalize_plan({})


async def run_agent_stream(
    text: str,
    current_user: dict | None = None,
    family_members: list | None = None,
    places: list | None = None,
    history: list | None = None,
    preference_memory: list | None = None,
    session_history: list | None = None,
    weather_memory: dict | None = None,
):
    async for event in _run_agent_events(
        text,
        current_user,
        family_members,
        places,
        history=history,
        preference_memory=preference_memory,
        session_history=session_history,
        weather_memory=weather_memory,
    ):
        yield event


# Backward-compatible helpers for older main.py versions.
def build_system_prompt(places: list | None, profile: dict | None) -> str:
    return build_agent_system_prompt()


def normalize_plan_response(plan_data: dict, profile: dict, intro_text: str = "") -> dict:
    if intro_text and isinstance(plan_data, dict):
        plan_data = {**plan_data, "intro": plan_data.get("intro") or intro_text}
    return normalize_plan(plan_data, fallback_profile=profile)


def debug_json(data: dict) -> str:
    return dumps(data)
