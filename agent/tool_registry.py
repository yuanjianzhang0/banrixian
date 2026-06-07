# -*- coding: utf-8 -*-
"""Tool registry and execution context."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from . import tools


@dataclass
class ToolContext:
    text: str
    current_user: dict | None = None
    family_members: list | None = None
    places: list | None = None
    current_time: dict | None = None
    goal: dict | None = None
    time_plan: dict | None = None
    risks: list = field(default_factory=list)
    profile: dict | None = None
    weather: dict | None = None
    preference_memory: list | None = None
    session_history: list = field(default_factory=list)
    search_results: list = field(default_factory=list)
    ranked_places: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    observations: list = field(default_factory=list)


def _extract_recently_used_places(session_history: list) -> set[str]:
    """从会话历史中解析最近推荐过的地点名，防止重复推同一批地方。"""
    names: set[str] = set()
    if not session_history:
        return names
    # assistant 摘要格式: "已生成规划：地点A + 地点B + 地点C，约下午半日路线。"
    plan_pattern = re.compile(r"已生成规划[：:](.*?)(?:[，,。]|$)")
    for item in session_history:
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        content = str(item.get("content") or "")
        m = plan_pattern.search(content)
        if m:
            for part in re.split(r"\s*[+→\-]\s*", m.group(1)):
                name = part.strip()
                if name and len(name) >= 2:
                    names.add(name)
    return names


TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "get_current_time": tools.get_current_time,
    "decompose_goal": tools.decompose_goal,
    "plan_time_slots": tools.plan_time_slots,
    "analyze_user_profile": tools.analyze_user_profile,
    "search_places": tools.search_places,
    "rank_places_for_plan": tools.rank_places_for_plan,
    "score_plans": tools.score_plans,
    "mock_reserve": tools.mock_reserve,
    "mock_create_order": tools.mock_create_order,
    "build_final_plan": tools.build_final_plan,
}


def _reservation_blocked(place: dict | None, people_count: int) -> str:
    if not place:
        return "地点不存在"
    return tools._place_reservation_blocked(place, people_count)


def _best_reservable_place(places: list[dict], people_count: int, preferred: dict | None = None) -> dict | None:
    preferred_category = preferred.get("category") if isinstance(preferred, dict) else ""
    preferred_is_food = bool(preferred and tools._category_is_food(preferred))

    def place_ok(place: dict) -> bool:
        return not _reservation_blocked(place, people_count)

    def same_kind(place: dict) -> bool:
        if preferred_is_food:
            return tools._category_is_food(place)
        if preferred_category:
            return place.get("category") == preferred_category
        return True

    return (
        next((p for p in places if same_kind(p) and place_ok(p)), None)
        or next((p for p in places if tools._category_is_food(p) and place_ok(p)), None)
        or next((p for p in places if place_ok(p)), None)
    )


def get_tool_specs() -> list[dict]:
    return [
        {
            "name": "get_current_time",
            "description": "获取服务器当前时间。默认 Asia/Beijing，返回 now_iso/date/weekday/hour/minute/period/readable。",
            "arguments": {"timezone": "Asia/Beijing"},
        },
        {
            "name": "decompose_goal",
            "description": "把用户请求拆成 time_window、participants、task_slots、hard_constraints、soft_constraints。",
            "arguments": {},
        },
        {
            "name": "plan_time_slots",
            "description": "结合 get_current_time 和 decompose_goal 生成不会落在过去的时间槽；如果用户时间已过，返回 warning 并自动顺延。",
            "arguments": {},
        },
        {
            "name": "analyze_user_profile",
            "description": "从用户输入中分析场景、人数、时间、距离、饮食、预算和偏好。",
            "arguments": {},
        },
        {
            "name": "search_places",
            "description": "从外部传入的真实 places 列表中检索地点，不直接连接数据库，不编造地点。",
            "arguments": {"keyword": "亲子/朋友/减肥/休闲/美食/约会/商务", "limit": 8},
        },
        {
            "name": "rank_places_for_plan",
            "description": "根据用户画像对 search_places 返回的真实候选地点排序并给出可解释原因。",
            "arguments": {"limit": 8},
        },
        {
            "name": "score_plans",
            "description": "把排序后的候选地点组合成 2-3 个方案，并给每个方案打分和风险评估。",
            "arguments": {"limit": 3},
        },
        {
            "name": "mock_reserve",
            "description": "模拟预约餐厅或景点。",
            "arguments": {"place_name": "地点名称", "time": "17:30", "people_count": 5},
        },
        {
            "name": "mock_create_order",
            "description": "模拟生成订单。",
            "arguments": {"target": "智能行程组合预订"},
        },
        {
            "name": "build_final_plan",
            "description": "基于 ranked_places 和 actions 整理最终前端可用 JSON，steps 只能来自真实候选地点。",
            "arguments": {},
        },
    ]


async def execute_tool(name: str, arguments: dict | None, context: ToolContext) -> dict:
    if name not in TOOL_FUNCTIONS:
        return {"ok": False, "error": f"Unknown tool: {name}"}

    arguments = dict(arguments or {})
    if name == "get_current_time":
        arguments.setdefault("timezone", "Asia/Beijing")
    elif name == "decompose_goal":
        if not arguments.get("text"):
            arguments["text"] = context.text
        arguments.setdefault("current_user", context.current_user)
    elif name == "plan_time_slots":
        if not arguments.get("text"):
            arguments["text"] = context.text
        if not arguments.get("current_time"):
            arguments["current_time"] = context.current_time
        if not arguments.get("goal"):
            arguments["goal"] = context.goal
    elif name == "analyze_user_profile":
        arguments.setdefault("text", context.text)
        arguments.setdefault("current_user", context.current_user)
        arguments.setdefault("family_members", context.family_members)
        arguments.setdefault("preference_memory", context.preference_memory)
    elif name == "search_places":
        arguments.setdefault("places", context.places)
    elif name == "rank_places_for_plan":
        arguments.setdefault("profile", context.profile or {})
        # Deliberately use search results only. If the LLM skips search_places,
        # ranking gets an empty list instead of the full DB snapshot.
        arguments.setdefault("places", context.search_results)
        arguments.setdefault("current_time", context.current_time)
        arguments.setdefault("time_plan", context.time_plan)
        # 注入最近推过的地点，排序时会对这些地点降权，避免每次推一样的
        arguments.setdefault("recently_used_names", _extract_recently_used_places(context.session_history))
    elif name == "score_plans":
        arguments.setdefault("ranked_places", context.ranked_places)
        arguments.setdefault("profile", context.profile or {})
        arguments.setdefault("time_plan", context.time_plan)
        arguments.setdefault("current_time", context.current_time)
        arguments.setdefault("limit", 3)
    elif name == "mock_reserve":
        valid_ranked = tools.normalize_places(context.ranked_places)
        valid_names = {place["name"] for place in valid_ranked}
        requested_name = arguments.get("place_name")
        people_count = int(arguments.get("people_count") or 1)
        requested_place = next((p for p in valid_ranked if p.get("name") == requested_name), None)
        if requested_name not in valid_names:
            replacement = _best_reservable_place(valid_ranked, people_count, requested_place)
            arguments["place_name"] = replacement["name"] if replacement else ""
        elif _reservation_blocked(requested_place, people_count):
            replacement = _best_reservable_place(valid_ranked, people_count, requested_place)
            if replacement:
                arguments["place_name"] = replacement["name"]
        target_name = arguments.get("place_name")
        target_place = next((p for p in valid_ranked if p.get("name") == target_name), None)
        if target_place:
            capacity_status = str(target_place.get("capacity_status") or "").strip().lower()
            available = target_place.get("available_seats")
            if available is not None and people_count > int(tools._float(available, 0)):
                return {
                    "ok": True,
                    "tool_name": name,
                    "result": {
                        "type": "reserve",
                        "target": target_name,
                        "status": "mock_failed",
                        "message": f"预约失败：{target_name} 可用座位不足，仅剩 {int(tools._float(available, 0))} 席。",
                    },
                }
            if capacity_status and capacity_status not in {"available", "open", "normal", "ok"}:
                return {
                    "ok": True,
                    "tool_name": name,
                    "result": {
                        "type": "reserve",
                        "target": target_name,
                        "status": "mock_failed",
                        "message": f"预约失败：{target_name} 当前状态为 {capacity_status}，可能无法预约。",
                    },
                }
    elif name == "mock_create_order":
        arguments.setdefault("target", "智能行程组合预订")
    elif name == "build_final_plan":
        arguments.setdefault("profile", context.profile or {})
        arguments.setdefault("ranked_places", context.ranked_places)
        arguments.setdefault("actions", context.actions)
        arguments.setdefault("observations", context.observations)
        if not arguments.get("current_time"):
            arguments["current_time"] = context.current_time
        if not arguments.get("goal"):
            arguments["goal"] = context.goal
        if not arguments.get("time_plan"):
            arguments["time_plan"] = context.time_plan
        arguments.setdefault("risks", context.risks)
        if not arguments.get("original_text"):
            arguments["original_text"] = context.text

    func = TOOL_FUNCTIONS[name]
    try:
        result = func(**arguments)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        return {"ok": False, "error": f"{name} failed: {exc}"}

    if name == "get_current_time" and isinstance(result, dict):
        context.current_time = result
    elif name == "decompose_goal" and isinstance(result, dict):
        context.goal = result
    elif name == "plan_time_slots" and isinstance(result, dict):
        context.time_plan = result
        warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
        for warning in warnings:
            if warning and warning not in context.risks:
                context.risks.append(str(warning))
    elif name == "analyze_user_profile" and isinstance(result, dict):
        context.profile = result
    elif name == "search_places" and isinstance(result, dict):
        context.search_results = result.get("places") or []
    elif name == "rank_places_for_plan" and isinstance(result, dict):
        context.ranked_places = result.get("ranked_places") or []
    elif name in {"mock_reserve", "mock_create_order"} and isinstance(result, dict):
        context.actions.append(result)
        if name == "mock_reserve" and result.get("status") == "mock_failed":
            message = result.get("message") or "预约失败"
            if message and message not in context.risks:
                context.risks.append(message)

    context.observations.append({"tool_name": name, "result": result})
    return {"ok": True, "tool_name": name, "result": result}
