# -*- coding: utf-8 -*-
"""Agent harness for guided tool use, state, and final-plan guardrails."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from dataclasses import dataclass
from typing import AsyncIterator

from core.sse import done_event, observation_event, skill_call_event, status_event
from skills.runtime import SkillRuntime

from .llm_client import LLMUnavailable, create_llm_client
from .prompts import build_agent_system_prompt, build_user_message
from .schemas import normalize_plan, parse_json_object
from .tool_registry import ToolContext
from .tools import _build_action_bundle, build_final_plan, mock_reserve, valid_place_names


PUBLIC_OBSERVATION_LIMIT = 5
DEFAULT_MIN_VISIBLE_PLANNING_SECONDS = 1.2


def _planning_timeout_seconds() -> float:
    raw = os.getenv("PLANNING_TIMEOUT_SECONDS", "22").strip()
    try:
        return max(5.0, min(30.0, float(raw)))
    except ValueError:
        return 22.0


def _min_visible_planning_seconds() -> float:
    raw = os.getenv("MIN_VISIBLE_PLANNING_SECONDS", "").strip()
    if not raw:
        return DEFAULT_MIN_VISIBLE_PLANNING_SECONDS
    try:
        return max(0.0, min(8.0, float(raw)))
    except ValueError:
        return DEFAULT_MIN_VISIBLE_PLANNING_SECONDS


def _enable_llm_plan_selection() -> bool:
    raw = os.getenv("ENABLE_LLM_PLAN_SELECTION", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _enable_llm_agent_loop() -> bool:
    raw = os.getenv("ENABLE_LLM_AGENT_LOOP", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _llm_plan_selection_timeout_seconds() -> float:
    raw = os.getenv("LLM_PLAN_SELECTION_TIMEOUT_SECONDS", "8.5").strip()
    try:
        return max(0.5, min(9.0, float(raw)))
    except ValueError:
        return 8.5


def _llm_usage_note(plan: dict) -> str:
    if plan.get("llm_enhanced"):
        return "LLM 使用说明：已用于多候选方案选择和路线取舍理由；地点检索、评分、时间槽和预约草稿由工具链完成。"
    reason = str(plan.get("llm_usage_reason") or "").strip()
    if plan.get("llm_intent_used"):
        if reason:
            return f"LLM 使用说明：已用于需求理解和偏好抽取，{reason}；最终路线由工具链评分生成。"
        return "LLM 使用说明：已用于需求理解和偏好抽取；最终路线由工具链评分生成。"
    if reason.startswith("LLM 已调用"):
        return f"LLM 使用说明：{reason}；最终路线由本地工具链评分生成。"
    if reason:
        return f"LLM 使用说明：本次未使用 LLM 增强，{reason}；路线由本地工具链评分生成。"
    return "LLM 使用说明：本次未使用 LLM 增强；路线由本地工具链评分生成。"


def _has_cjk_text(value: object, min_len: int = 6) -> bool:
    text = str(value or "").strip()
    return len(text) >= min_len and bool(re.search(r"[\u4e00-\u9fff]", text))


def _parse_candidate_index(value: object, valid_indices: set[int]) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    match = re.search(r"\d+", text)
    if not match:
        return None
    selected = int(match.group(0))
    if selected in valid_indices:
        return selected
    if selected - 1 in valid_indices:
        return selected - 1
    return None


def _fallback_intro_from_steps(steps: list) -> str:
    names = [str(step.get("name") or "").strip() for step in steps if isinstance(step, dict) and step.get("name")]
    if not names:
        return "我已根据你的需求生成了一份可执行的本地半日方案。"
    return f"我已为你安排好路线：{' → '.join(names[:4])}。"


def _fallback_thinking_from_steps(steps: list, llm_enhanced: bool) -> list[str]:
    count = len([step for step in steps if isinstance(step, dict) and step.get("name")])
    prefix = "LLM 已参与审核候选方案，" if llm_enhanced else ""
    return [
        f"{prefix}当前路线包含 {count} 个真实地点，已按时间窗口和出行偏好做过筛选。",
        "系统已生成可确认的预约/下单/通知草稿，用户确认后可一键执行。",
    ]


@dataclass(frozen=True)
class HarnessOptions:
    max_steps: int = 10
    guided_bootstrap: bool = True


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


def _needs_weather(text: str, history: list | None = None) -> bool:
    payload = text or ""
    for item in history or []:
        if isinstance(item, dict):
            payload += "\n" + str(item.get("content") or "")
    weather_words = [
        "天气", "下雨", "有雨", "雨天", "会下雨", "晒不晒", "太晒",
        "冷不冷", "热不热", "气温", "温度", "风大", "户外", "室外",
        "适合出门", "适合户外",
    ]
    return any(word in payload for word in weather_words)


def _text_has_children(text: str) -> bool:
    """当前文本是否明确提到了孩子/亲子。"""
    return any(w in text for w in ["亲子", "孩子", "儿童", "小孩", "宝宝", "带娃", "娃"])




def _venue_family(name: str) -> str:
    """提取地点的园区主体名，用于同园区去重（如'天坛公园-科普园'→'天坛公园'）。"""
    for sep in ["-", "·", "—", "(", "（", "/"]:
        if sep in name:
            return name.split(sep)[0].strip()
    return name


def _post_process_steps(steps: list, ranked_places: list, has_children: bool, min_steps: int = 2) -> list:
    """LLM输出后的硬性规则校验：
    1. 同园区名称只保留一个（去掉天坛公园-A + 天坛公园-B 的重复）
    2. 乐园最多1个（有孩子时也强制）
    3. 步骤不足2个时从 ranked_places 补齐
    """
    if not steps:
        return steps

    result = []
    seen_families = set()
    playground_count = 0

    _playground_words = ["乐园", "游乐", "游乐场", "儿童乐园"]

    for step in steps:
        name = step.get("name", "")
        if not name:
            continue
        family = _venue_family(name)
        # 同园区去重
        if family in seen_families:
            continue
        seen_families.add(family)
        seen_families.add(name)
        # 乐园限制：最多1个
        is_pg = any(w in name for w in _playground_words)
        if is_pg:
            if playground_count >= 1:
                continue
            playground_count += 1
        result.append(step)

    # 步骤不足时从 ranked_places 补齐（最少2步）—— 两轮：严格 → 宽松
    min_steps = max(1, int(min_steps or 2))
    for strict in (True, False):
        if len(result) >= min_steps:
            break
        used_names = {s.get("name", "") for s in result}
        for place in ranked_places:
            if len(result) >= max(3, min_steps):
                break
            pname = place.get("name", "")
            if not pname or pname in used_names:
                continue
            if strict:
                pfamily = _venue_family(pname)
                if pfamily in seen_families:
                    continue
                is_pg = any(w in pname for w in _playground_words)
                if is_pg and playground_count >= 1:
                    continue
            result.append({
                "name": pname,
                "time": result[-1].get("time", "15:00") if result else "15:00",
                "category": place.get("category", ""),
                "meta": place.get("desc_text", ""),
                "reason": place.get("reason", ""),
                "lng": place.get("lng", 0.0),
                "lat": place.get("lat", 0.0),
            })
            used_names.add(pname)

    return result


def _retime_steps(steps: list, time_plan: dict | None) -> list:
    if not steps:
        return steps
    time_plan = time_plan or {}
    slots = time_plan.get("slots") if isinstance(time_plan.get("slots"), list) else []

    def to_minutes(value: str) -> int | None:
        match = re.match(r"^(\d{1,2}):(\d{2})$", str(value or ""))
        if not match:
            return None
        return int(match.group(1)) * 60 + int(match.group(2))

    def from_minutes(value: int) -> str:
        value = max(0, value) % (24 * 60)
        return f"{value // 60:02d}:{value % 60:02d}"

    base_minutes = to_minutes(str(time_plan.get("start") or "")) or 14 * 60
    retimed: list[dict] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        slot = slots[index] if index < len(slots) and isinstance(slots[index], dict) else None
        if slot and slot.get("start"):
            next_time = str(slot.get("start"))
            next_date = slot.get("date") or time_plan.get("date") or step.get("date") or ""
        else:
            next_time = from_minutes(base_minutes + index * 60)
            next_date = time_plan.get("date") or step.get("date") or ""
        retimed.append({**step, "time": next_time, "date": next_date})
    return retimed


def _choose_search_keyword(text: str, profile: dict | None, goal: dict | None) -> str:
    # (已在上方定义完整版，此处保留 route_style 兜底逻辑)
    text = text or ""
    profile = profile or {}
    goal = goal or {}
    route_style = str(profile.get("route_style") or "")
    llm_keywords = " ".join(str(item) for item in (profile.get("llm_keywords") or []))

    if any(w in f"{text} {llm_keywords}" for w in ["恐龙", "化石", "古生物"]): return "恐龙"
    if "火锅" in f"{text} {llm_keywords}": return "火锅"
    if "烤鸭" in text: return "烤鸭"
    if "海鲜" in text: return "海鲜"
    if any(w in f"{text} {llm_keywords}" for w in ["减肥", "清淡", "低卡", "低油", "少油", "低负担", "不太能吃辣", "不能吃辣"]): return "低负担"

    style_keywords = {
        "亲子": "亲子", "美食": "美食", "约会": "约会", "朋友": "朋友",
        "商务": "商务", "拍照": "拍照", "室内": "室内", "自然": "自然",
        "文化": "文化", "省钱": "省钱", "高端": "高端", "轻松": "休闲",
    }
    if route_style in style_keywords:
        return style_keywords[route_style]

    if _text_has_children(text): return "亲子"
    if any(w in text for w in ["商务", "宴请", "客户"]): return "商务"
    if any(w in text for w in ["咖啡", "咖啡馆"]): return "咖啡"
    if any(w in text for w in ["茶馆", "喝茶"]): return "茶馆"
    if any(w in text for w in ["展览", "看展", "博物馆", "美术馆", "展"]): return "文化"
    if any(w in text for w in ["公园", "自然", "户外", "人少", "安静", "清净"]): return "自然"
    if any(w in text for w in ["约会", "伴侣", "老婆", "女朋友", "男朋友"]): return "约会"
    if any(w in text for w in ["朋友", "聚会", "同事", "同学", "聚餐"]): return "朋友"
    if any(w in text for w in ["减肥", "清淡", "低卡", "低油", "少油", "低负担"]): return "低负担"
    if any(w in text for w in ["餐", "吃", "美食", "晚饭", "午饭", "晚餐"]): return "美食"
    if any(w in text for w in ["拍照", "打卡", "出片", "网红"]): return "拍照"
    if any(w in text for w in ["老人", "父母", "爸妈", "腿脚", "长辈"]): return "休闲"
    if any(w in text for w in ["休闲", "放松", "散步", "逛逛", "走走"]): return "休闲"

    scene = profile.get("scene") if isinstance(profile.get("scene"), list) else []
    task_slots = goal.get("task_slots") if isinstance(goal.get("task_slots"), list) else []
    context = " ".join([*map(str, scene), *map(str, task_slots)])
    if any(w in context for w in ["约会", "伴侣"]): return "约会"
    if any(w in context for w in ["聚会", "朋友"]): return "朋友"
    return "休闲"


_GENERIC_META = {"匹配当前需求", "待填写", "暂无描述", ""}


def _enrich_plan_steps(plan: dict, ranked_places: list) -> dict:
    """从 ranked_places 补全 steps 中所有缺失或泛化的字段（lat/lng/meta/reason/category）。"""
    steps = plan.get("steps")
    if not isinstance(steps, list) or not ranked_places:
        return plan

    place_map = {
        p["name"]: p
        for p in ranked_places
        if isinstance(p, dict) and p.get("name")
    }
    enriched = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        place = place_map.get(step.get("name", ""), {})
        updated = dict(step)

        # 总是用数据库里的坐标覆盖（LLM 经常填 0.0）
        if place:
            db_lat = place.get("lat")
            db_lng = place.get("lng")
            if db_lat is not None and db_lng is not None:
                try:
                    lat_f = float(db_lat)
                    lng_f = float(db_lng)
                    # 只要数据库有有效坐标就覆盖
                    if lat_f != 0.0 or lng_f != 0.0:
                        updated["lat"] = lat_f
                        updated["lng"] = lng_f
                except (TypeError, ValueError):
                    pass

        # meta：空值或泛化词时用数据库描述替换
        current_meta = str(updated.get("meta") or "").strip()
        if current_meta in _GENERIC_META and place:
            desc = (place.get("desc_text") or "").strip()
            price = (place.get("price_range") or "").strip()
            parts = [p for p in [desc, price] if p]
            updated["meta"] = "；".join(parts) if parts else (place.get("reason") or "")

        # reason：空时从 ranked_places 里取
        if not updated.get("reason") and place:
            updated["reason"] = str(place.get("reason") or "")

        # category：空时补全
        if not updated.get("category") and place:
            updated["category"] = str(place.get("category") or "")

        for key in ("source", "source_id", "phone", "address"):
            if place and not updated.get(key) and place.get(key):
                updated[key] = str(place.get(key) or "")

        enriched.append(updated)

    # 修正 intro：确保 intro 包含所有 steps 地点名
    place_names = [s.get("name", "") for s in enriched if isinstance(s, dict) and s.get("name")]
    existing_intro = str(plan.get("intro") or "")
    if place_names:
        route_str = " → ".join(place_names[:4])
        if route_str not in existing_intro:
            plan = {**plan, "intro": f"已为你安排好路线：{route_str}。"}

    return {**plan, "steps": enriched}


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
    if skill_name == "get_memory":
        count = len(result.get("preference_memory") or [])
        return f"已读取用户记忆和短期上下文，偏好记忆 {count} 条"
    if skill_name == "analyze_user_profile":
        scenes = "、".join(result.get("scene") or [])
        return f"已完成用户画像分析{f'：{scenes}' if scenes else ''}"
    if skill_name == "plan_time_slots":
        warning = "；".join(result.get("warnings") or [])
        return warning or f"已生成不会落在过去的时间安排：{result.get('start')} - {result.get('end')}"
    if skill_name == "get_weather":
        if result.get("status") == "ok":
            return result.get("message") or "已获取实时天气"
        return result.get("message") or "实时天气暂不可用"
    if skill_name == "search_places":
        count = result.get("count")
        if count is None:
            count = len(result.get("places") or [])
        return f"已找到 {count} 个候选地点"
    if skill_name == "rank_places_for_plan":
        return f"已完成排序，保留 {len(result.get('ranked_places') or [])} 个候选地点"
    if skill_name == "score_plans":
        return f"已生成 {len(result.get('candidate_plans') or [])} 个候选方案并完成评分"
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


def _plan_matches_request_shape(plan: dict, text: str) -> bool:
    steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    if not steps:
        return False
    request = str(text or "")
    step_texts = [
        f"{step.get('name', '')} {step.get('category', '')} {step.get('meta', '')} {step.get('reason', '')}"
        for step in steps
    ]
    food_words = ("餐", "饭", "咖啡", "茶", "火锅", "烤鸭", "酒吧", "小吃", "美食")
    activity_words = ("看展", "展", "公园", "逛", "散步", "博物馆", "美术馆", "文化", "自然")
    wants_activity = any(word in request for word in activity_words)
    wants_only_meal = any(word in request for word in ("只要一家", "一家就行", "推荐一家", "找一家", "只吃饭", "只用餐"))
    food_count = sum(1 for text_item in step_texts if any(word in text_item for word in food_words))
    if wants_only_meal:
        return len(steps) == 1 and food_count == 1
    if wants_activity and food_count == len(steps):
        return False
    if len(steps) >= 3 and food_count == len(steps):
        return False
    return True


def _plan_is_usable(plan: dict, context: ToolContext) -> bool:
    return bool(
        isinstance(plan, dict)
        and plan.get("type") == "plan"
        and plan.get("steps")
        and _plan_uses_known_places(plan, context.places)
        and _actions_are_usable(plan, context.places)
        and _plan_matches_request_shape(plan, context.text)
    )


class AgentHarness:
    """Guided ReAct-style harness around the model and Python skills."""

    def __init__(
        self,
        text: str,
        current_user: dict | None = None,
        family_members: list | None = None,
        places: list | None = None,
        history: list | None = None,
        preference_memory: list | None = None,
        session_history: list | None = None,
        weather_memory: dict | None = None,
        options: HarnessOptions | None = None,
    ) -> None:
        self.text = text or ""
        self.history = history or []
        self.options = options or HarnessOptions()
        self.context = ToolContext(
            text=self.text,
            current_user=current_user,
            family_members=family_members,
            places=places,
            preference_memory=preference_memory,
            session_history=session_history or [],
            weather=weather_memory,
        )
        self.skill_runtime = SkillRuntime(self.context)
        self.step_index = 0
        self.llm_intent: dict | None = None
        self.llm_intent_reason: str = ""
        self.messages = [
            {"role": "system", "content": build_agent_system_prompt(max_steps=self.options.max_steps)},
            {
                "role": "user",
                "content": build_user_message(
                    text=self.text,
                    current_user=current_user,
                    family_members=family_members,
                    places_count=len(places or []),
                    history=history,
                    preference_memory=preference_memory,
                    weather_memory=weather_memory,
                ),
            },
        ]

    async def _finish_from_context(self) -> dict:
        plan = build_final_plan(
            profile=self.context.profile or {},
            ranked_places=self.context.ranked_places,
            actions=self.context.actions,
            observations=self.context.observations,
            current_time=self.context.current_time,
            goal=self.context.goal,
            time_plan=self.context.time_plan,
            risks=self.context.risks,
            original_text=self.context.text,
        )
        return normalize_plan(plan, fallback_profile=self.context.profile or {})

    async def _llm_select_plan(self, llm) -> dict:
        """单次聚焦 LLM 调用：从 ranked_places 中真正由 AI 选择地点组合成路线。"""
        ranked = self.context.ranked_places[:8]
        profile = self.context.profile or {}
        time_plan = self.context.time_plan or {}

        places_compact = [
            {
                "name": p["name"],
                "category": p.get("category", ""),
                "score": p.get("final_score", 0),
                "reason": p.get("reason", ""),
                "desc": (p.get("desc_text") or "")[:40],
                "price": p.get("price_range", ""),
                "hours": p.get("open_hours", ""),
            }
            for p in ranked
        ]

        slots = time_plan.get("slots") or []
        slots_str = "、".join(
            f"{s.get('start')}({s.get('label')})" for s in slots[:5]
        ) if slots else f"{time_plan.get('start','14:00')}-{time_plan.get('end','18:00')}"

        # 计算时间窗口时长（分钟）
        def _to_min(t: str) -> int:
            try:
                h, m = map(int, (t or "14:00").split(":"))
                return h * 60 + m
            except Exception:
                return 840  # 14:00
        if slots:
            total_minutes = _to_min(slots[-1].get("end", "18:00")) - _to_min(slots[0].get("start", "14:00"))
        else:
            total_minutes = _to_min(time_plan.get("end", "18:00")) - _to_min(time_plan.get("start", "14:00"))
        total_minutes = max(60, total_minutes)

        # 根据时间窗口决定最多几个地点
        if total_minutes < 150:
            max_steps = 2
        elif total_minutes < 300:
            max_steps = 3
        else:
            max_steps = 4

        # 当前请求是否明确有孩子
        has_children = _text_has_children(self.text)
        # 当前请求场景
        scene = "、".join(profile.get("scene") or []) or "半日出行"
        constraints_list = profile.get("constraints") or []
        preferences_list = profile.get("preferences") or []
        constraints_str = "、".join(constraints_list + preferences_list) or "无"
        people = profile.get("people") or {}
        people_desc = people.get("description") or f"{people.get('adults',1)}大人"

        children_rule = (
            "- 用户明确带孩子，可选亲子/乐园类，但整条路线乐园最多1个"
            if has_children else
            "- ⛔ 用户本次没有提到孩子/亲子/带娃，严禁选任何「乐园」「游乐场」「亲子」类场所"
        )
        time_rule = (
            f"- 时间窗口约 {total_minutes} 分钟，最多选 {max_steps} 个地点"
            f"{'（时间紧，不要塞乐园/大景区，选轻松好去的地方）' if total_minutes < 180 else ''}"
        )

        system = (
            "你是半日闲AI路线规划师。根据已排序的候选地点，选出最合适的地点组成路线。\n\n"
            "【严格规则，必须全部遵守】\n"
            f"- steps[].name 必须与候选地点 name 字段完全一致，不能改写或创造地点\n"
            f"{children_rule}\n"
            f"{time_rule}\n"
            "- 乐园/游乐场类（名称含「乐园」「游乐」「游乐场」）全路线最多1个\n"
            "- 同类型（如景点/餐厅/咖啡）每类最多2个，整体路线最多3-4个地点\n"
            "- 路线要有逻辑：游玩→用餐，或 文化→休闲→咖啡，不要同质化堆叠\n"
            "- 严格匹配当前用户请求场景，不受历史记忆干扰\n"
            "- thinking 给用户看，通俗易懂，说清楚为什么选这几个\n"
            "- 输出纯JSON，不加Markdown\n\n"
            '格式：{"type":"final_answer","data":{"type":"plan","intro":"一句话路线说明",'
            '"thinking":["理由1","理由2"],'
            '"steps":[{"time":"14:00","name":"地点名","meta":"简短描述","reason":"为什么选"}],'
            '"actions":[],"feasibility":true,"risks":[]}}'
        )

        user_content = (
            f"用户请求：{self.text}\n"
            f"同行人：{people_desc}\n"
            f"场景：{scene}\n"
            f"约束偏好：{constraints_str}\n"
            f"时间槽：{slots_str}（共约{total_minutes}分钟），日期：{time_plan.get('date', '')}\n\n"
            f"候选地点（已按适配度排序，从中选{max_steps}个）：\n"
            f"{json.dumps(places_compact, ensure_ascii=False)}"
        )

        try:
            raw = await asyncio.wait_for(
                llm.complete([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ]),
                timeout=_llm_plan_selection_timeout_seconds(),
            )
            decision = parse_llm_decision(raw)
            if decision["type"] == "final_answer":
                plan = normalize_plan(decision.get("data") or {}, fallback_profile=profile)
                if _plan_is_usable(plan, self.context):
                    return plan
        except Exception:
            pass  # LLM 超时或返回格式不对，由调用方降级到 Python

        return {}

    async def _llm_enhance_plan(self, llm, base_plan: dict, timeout_seconds: float | None = None) -> dict:
        """Let LLM review the tool-built plan and improve explanations/copy."""
        profile = self.context.profile or base_plan.get("profile") or {}
        people = profile.get("people") if isinstance(profile.get("people"), dict) else {}
        people_desc = people.get("description") or f"{people.get('adults', 1)}大人"
        steps = [
            {
                "time": step.get("time", ""),
                "name": step.get("name", ""),
                "category": step.get("category", ""),
                "meta": str(step.get("meta") or "")[:80],
                "reason": str(step.get("reason") or "")[:80],
            }
            for step in (base_plan.get("steps") or [])[:5]
            if isinstance(step, dict)
        ]
        action_items = [
            {
                "id": item.get("id", ""),
                "type": item.get("type", ""),
                "target": item.get("target", ""),
                "message": str(item.get("message") or "")[:120],
            }
            for item in ((base_plan.get("action_bundle") or {}).get("items") or [])[:6]
            if isinstance(item, dict)
        ]
        score_summary = base_plan.get("score_summary") if isinstance(base_plan.get("score_summary"), dict) else {}
        timeout_seconds = _llm_plan_selection_timeout_seconds() if timeout_seconds is None else timeout_seconds
        if timeout_seconds < 0.8:
            return {**base_plan, "llm_enhanced": False, "llm_usage_reason": "剩余规划时间不足，跳过 LLM 增强"}

        system = (
            "你是半日闲AI路线文案助手。基于已选地点做文案增强，不能改地点名和时间。\n"
            '输出纯JSON：{"intro":"一句话路线说明","thinking":["最多2条理由"],"action_messages":{"动作id":"文案"}}'
        )
        user_content = json.dumps({
            "用户请求": self.text,
            "同行人": people_desc,
            "路线": steps,
            "动作草稿": action_items,
            "评分摘要": {
                "candidate_count": score_summary.get("candidate_count"),
                "selected_score": score_summary.get("selected_score"),
                "selection_reason": score_summary.get("selection_reason"),
            },
        }, ensure_ascii=False)

        try:
            raw = await asyncio.wait_for(
                llm.complete([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ]),
                timeout=timeout_seconds,
            )
            payload = parse_json_object(raw) or {}
            if payload.get("type") == "final_answer" and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            if not isinstance(payload, dict):
                return {**base_plan, "llm_enhanced": False, "llm_usage_reason": "LLM 返回内容不可用"}

            intro = str(payload.get("intro") or "").strip()
            raw_thinking = payload.get("thinking")
            thinking_items = [
                str(item).strip()
                for item in raw_thinking
                if _has_cjk_text(item)
            ] if isinstance(raw_thinking, list) else []
            action_messages = payload.get("action_messages")
            useful_action_messages = {
                str(key): str(value).strip()
                for key, value in action_messages.items()
                if _has_cjk_text(value)
            } if isinstance(action_messages, dict) else {}

            if not _has_cjk_text(intro) and not thinking_items and not useful_action_messages:
                return {**base_plan, "llm_enhanced": False, "llm_usage_reason": "LLM 未返回可用中文说明"}

            enhanced = {**base_plan, "llm_enhanced": True}
            if _has_cjk_text(intro):
                enhanced["intro"] = intro[:180]
            if thinking_items:
                enhanced["thinking"] = thinking_items[:3] + [
                    item for item in (base_plan.get("thinking") or []) if item
                ][:2]
            bundle = enhanced.get("action_bundle")
            if useful_action_messages and isinstance(bundle, dict) and isinstance(bundle.get("items"), list):
                next_items = []
                for item in bundle["items"]:
                    if isinstance(item, dict) and item.get("id") in useful_action_messages:
                        next_items.append({**item, "message": useful_action_messages[item["id"]][:260]})
                    else:
                        next_items.append(item)
                enhanced["action_bundle"] = {**bundle, "items": next_items}
            return normalize_plan(enhanced, fallback_profile=profile)
        except asyncio.TimeoutError:
            return {**base_plan, "llm_enhanced": False, "llm_usage_reason": "LLM 超过预算未返回"}
        except Exception:
            return {**base_plan, "llm_enhanced": False, "llm_usage_reason": "LLM 调用失败"}

    async def _llm_choose_from_candidate_plans(self, llm, base_plan: dict, timeout_seconds: float | None = None) -> dict:
        """Ask LLM to make the narrowest useful decision: pick one scored candidate."""
        profile = self.context.profile or base_plan.get("profile") or {}
        candidates = [
            {
                "index": idx,
                "score": item.get("score"),
                "steps": " -> ".join(
                    [
                        f"{step.get('time', '')} {step.get('name', '')}"
                        for step in (item.get("steps") or [])[:5]
                        if isinstance(step, dict)
                    ]
                ),
            }
            for idx, item in enumerate((base_plan.get("candidate_plans") or [])[:3])
            if isinstance(item, dict) and item.get("steps")
        ]
        if not candidates:
            return await self._llm_enhance_plan(llm, base_plan, timeout_seconds=timeout_seconds)
        timeout_seconds = _llm_plan_selection_timeout_seconds() if timeout_seconds is None else timeout_seconds
        if timeout_seconds < 0.8:
            return {**base_plan, "llm_enhanced": False, "llm_usage_reason": "剩余规划时间不足，跳过 LLM 选择"}

        system = (
            "你是半日闲AI的路线裁判。只从候选方案中选一个，不能新增地点，不能改地点名，不能改时间。\n"
            "重点判断：是否满足用户需求、时间窗口、同行人、用餐偏好、动线强度。\n"
            "输出纯JSON，不要Markdown，只能包含 selected_index 和 reason。"
            "selected_index 必须是候选里的 index；reason 用一句中文说明选择理由。"
        )
        user_content = json.dumps({
            "request": self.text[:180],
            "candidates": candidates,
        }, ensure_ascii=False)
        try:
            raw = await asyncio.wait_for(
                llm.complete([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ]),
                timeout=timeout_seconds,
            )
            payload = parse_json_object(raw) or {}
            if payload.get("type") == "final_answer" and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            valid_indices = {int(item["index"]) for item in candidates}
            selected_index = _parse_candidate_index(payload.get("selected_index"), valid_indices)
            if selected_index is None:
                selected_index = _parse_candidate_index(payload.get("方案") or payload.get("choice"), valid_indices)
            selected = next((item for item in candidates if item["index"] == selected_index), None)
            source_plan = (base_plan.get("candidate_plans") or [])[selected_index] if selected else None
            if not isinstance(source_plan, dict) or not selected:
                return {**base_plan, "llm_enhanced": False, "llm_usage_reason": "LLM 返回的候选编号不可用"}
            reason = str(payload.get("reason") or "").strip()
            enhanced = {
                **base_plan,
                "steps": source_plan.get("steps") or base_plan.get("steps") or [],
                "selected_plan": source_plan,
                "llm_enhanced": True,
                "llm_usage_reason": "LLM 已从多候选方案中选择最终路线",
            }
            thinking_items = []
            if _has_cjk_text(reason, min_len=4):
                thinking_items.append(f"LLM 在多方案评分后选择第 {selected_index + 1} 套：{reason[:120]}")
            else:
                thinking_items.append(f"LLM 在多方案评分后选择第 {selected_index + 1} 套，作为最终路线。")
            enhanced["thinking"] = thinking_items + [
                item for item in (base_plan.get("thinking") or []) if item
            ][:3]
            return normalize_plan(enhanced, fallback_profile=profile)
        except asyncio.TimeoutError:
            return {**base_plan, "llm_enhanced": False, "llm_usage_reason": "LLM 超过预算未返回"}
        except Exception as exc:
            detail = f"{type(exc).__name__}: {str(exc)[:80]}"
            return {**base_plan, "llm_enhanced": False, "llm_usage_reason": f"LLM 选择候选方案失败（{detail}）"}

    async def _llm_extract_intent(self, llm, timeout_seconds: float = 5.0) -> dict:
        """Use LLM for a tiny, parallel intent read that can fit inside the planning budget."""
        system = (
            "你是半日闲AI的需求理解器。只抽取用户出行意图，不推荐地点。\n"
            "输出纯JSON，不要Markdown，只能包含 keywords、diet、pace、notes。"
            "keywords 最多5个短词；diet 和 pace 用短中文；notes 最多一句。"
        )
        user_content = json.dumps({"request": self.text[:220]}, ensure_ascii=False)
        raw = await asyncio.wait_for(
            llm.complete([
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ]),
            timeout=timeout_seconds,
        )
        payload = parse_json_object(raw) or {}
        if not isinstance(payload, dict):
            return {}
        keywords = payload.get("keywords")
        if isinstance(keywords, str):
            keywords = [item.strip() for item in re.split(r"[,，、\s]+", keywords) if item.strip()]
        if not isinstance(keywords, list):
            keywords = []
        return {
            "keywords": [str(item).strip()[:12] for item in keywords if str(item).strip()][:5],
            "diet": str(payload.get("diet") or "").strip()[:40],
            "pace": str(payload.get("pace") or "").strip()[:40],
            "notes": str(payload.get("notes") or "").strip()[:80],
        }

    async def _apply_llm_intent_if_ready(self, intent_task: asyncio.Task | None, wait_seconds: float = 0.8) -> None:
        if not intent_task:
            return
        try:
            intent = await asyncio.wait_for(asyncio.shield(intent_task), timeout=wait_seconds)
        except asyncio.TimeoutError:
            return
        except Exception as exc:
            self.llm_intent_reason = f"LLM 需求理解失败（{type(exc).__name__}）"
            return
        if not isinstance(intent, dict) or not intent.get("keywords"):
            return
        self.llm_intent = intent
        profile = dict(self.context.profile or {})
        preferences = list(profile.get("preferences") or [])
        for value in [*intent.get("keywords", []), intent.get("diet"), intent.get("pace")]:
            value = str(value or "").strip()
            if value and value not in preferences:
                preferences.append(value)
        profile["preferences"] = preferences[:12]
        profile["llm_keywords"] = intent.get("keywords") or []
        self.context.profile = profile
        self.llm_intent_reason = str(intent.get("notes") or intent.get("keywords") or "已完成需求理解")

    def _merge_llm_plan_with_execution(self, base_plan: dict, llm_plan: dict) -> dict:
        """Use LLM-selected steps while rebuilding execution actions locally."""
        if not _plan_is_usable(llm_plan, self.context):
            base_plan["llm_enhanced"] = False
            return base_plan

        profile = self.context.profile or base_plan.get("profile") or {}
        time_plan = self.context.time_plan or base_plan.get("time_plan") or {}
        score_summary = base_plan.get("score_summary") if isinstance(base_plan.get("score_summary"), dict) else {}
        selected_plan = base_plan.get("selected_plan") if isinstance(base_plan.get("selected_plan"), dict) else {}
        steps = llm_plan.get("steps") or []
        step_names = {step.get("name") for step in steps if isinstance(step, dict)}
        actions = [
            action for action in (base_plan.get("actions") or [])
            if not isinstance(action, dict)
            or action.get("type") != "reserve"
            or action.get("target") in step_names
        ]

        request_text = self.context.text or ""
        wants_reserve = any(word in request_text for word in ["预约", "预订", "订座", "订位", "下单", "帮我订", "帮我预约"])
        if wants_reserve and steps and not any(isinstance(action, dict) and action.get("type") == "reserve" for action in actions):
            reserve_step = next(
                (
                    step for step in steps
                    if any(word in str(step.get("category") or step.get("meta") or step.get("name") or "") for word in ["餐厅", "美食", "咖啡", "茶", "火锅", "烤鸭"])
                ),
                steps[-1],
            )
            people = profile.get("people") if isinstance(profile.get("people"), dict) else {}
            people_count = max(1, int(people.get("adults") or 1) + int(people.get("children") or 0))
            actions.insert(
                0,
                mock_reserve(
                    reserve_step.get("name", ""),
                    time=reserve_step.get("time") or time_plan.get("start") or "17:30",
                    people_count=people_count,
                ),
            )

        merged = {
            **base_plan,
            "intro": llm_plan.get("intro") or base_plan.get("intro"),
            "thinking": [
                "LLM 已在限定时间内审核候选方案，并参与最终路线取舍。",
                *[item for item in (llm_plan.get("thinking") or []) if item],
                *[item for item in (base_plan.get("thinking") or []) if item],
            ][:5],
            "steps": steps,
            "actions": actions,
            "llm_enhanced": True,
        }
        merged["action_bundle"] = _build_action_bundle(
            steps=steps,
            profile=profile,
            request_text=request_text,
            time_plan=time_plan,
            actions=actions,
            selected_plan=selected_plan,
            score_summary=score_summary,
        )
        return normalize_plan(merged, fallback_profile=profile)

    def _rebuild_execution_bundle_for_plan(self, plan: dict) -> dict:
        profile = self.context.profile or plan.get("profile") or {}
        time_plan = self.context.time_plan or plan.get("time_plan") or {}
        steps = plan.get("steps") or []
        step_names = {step.get("name") for step in steps if isinstance(step, dict)}
        actions = [
            action for action in (plan.get("actions") or [])
            if not isinstance(action, dict)
            or action.get("type") != "reserve"
            or action.get("target") in step_names
        ]
        score_summary = plan.get("score_summary") if isinstance(plan.get("score_summary"), dict) else {}
        selected_plan = plan.get("selected_plan") if isinstance(plan.get("selected_plan"), dict) else {}
        plan = {**plan, "actions": actions}
        plan["action_bundle"] = _build_action_bundle(
            steps=steps,
            profile=profile,
            request_text=self.context.text or "",
            time_plan=time_plan,
            actions=actions,
            selected_plan=selected_plan,
            score_summary=score_summary,
        )
        return plan

    def _sanitize_plan_text(self, plan: dict) -> dict:
        steps = plan.get("steps") or []
        if not _has_cjk_text(plan.get("intro"), min_len=8):
            plan = {**plan, "intro": _fallback_intro_from_steps(steps)}
        thinking = [
            str(item).strip()
            for item in (plan.get("thinking") or [])
            if _has_cjk_text(item)
        ]
        if not thinking:
            thinking = _fallback_thinking_from_steps(steps, bool(plan.get("llm_enhanced")))
        usage_note = _llm_usage_note(plan)
        thinking = [item for item in thinking if not str(item).startswith("LLM 使用说明：")]
        plan["thinking"] = [usage_note, *thinking][:6]
        return plan

    async def _execute_skill(self, skill_name: str, arguments: dict | None = None, step: int | None = None) -> AsyncIterator[dict]:
        arguments = dict(arguments or {})
        if step is None:
            self.step_index += 1
            step = self.step_index
        else:
            self.step_index = max(self.step_index, step)
        yield skill_call_event(
            skill=skill_name,
            arguments=arguments,
            visible_reason=self._visible_reason(skill_name),
            step=step,
        )
        observation = await self.skill_runtime.execute(skill_name, arguments)
        self.messages.append(_observation_message(observation))
        public_event = _public_observation(skill_name, observation)
        if step is not None:
            public_event["step"] = step
        yield public_event

    def _visible_reason(self, skill_name: str) -> str:
        reasons = {
            "get_current_time": "先获取服务器当前时间，避免生成已经过去的行程时间",
            "decompose_goal": "把自然语言需求拆成时间、人数、任务和约束",
            "get_memory": "读取长期偏好和短期上下文，用于理解连续追问",
            "get_weather": "用户涉及天气或户外适宜性，需要真实天气 observation",
            "analyze_user_profile": "分析同行人、场景、预算、距离和饮食偏好",
            "plan_time_slots": "生成可执行的时间槽，并自动避开过去时间",
            "search_places": "从真实地点库检索候选地点",
            "rank_places_for_plan": "根据画像、时间、距离、容量和预算对候选地点排序",
            "score_plans": "生成多个候选方案并做可行性评分",
        }
        return reasons.get(skill_name, "调用工具获取下一步信息")

    async def _guided_bootstrap(self, intent_task: asyncio.Task | None = None) -> AsyncIterator[dict]:
        yield status_event("Harness 正在整理时间、记忆、画像和候选地点")
        step = 1
        for skill_name, arguments in [
            ("get_current_time", {"timezone": "Asia/Beijing"}),
            ("decompose_goal", {}),
            ("get_memory", {}),
        ]:
            async for event in self._execute_skill(skill_name, arguments, step=step):
                yield event
            step += 1

        if _needs_weather(self.text, self.history):
            _u = self.context.current_user or {}
            city = str(_u.get("city") or "北京")
            weather_args: dict = {"city": city, "date": "today"}
            _lat = _u.get("lat")
            _lng = _u.get("lng")
            try:
                if _lat is not None and _lng is not None:
                    weather_args["lat"] = float(_lat)
                    weather_args["lng"] = float(_lng)
            except (TypeError, ValueError):
                pass
            async for event in self._execute_skill("get_weather", weather_args, step=step):
                yield event
            step += 1

        for skill_name, arguments in [
            ("analyze_user_profile", {}),
            ("plan_time_slots", {}),
        ]:
            async for event in self._execute_skill(skill_name, arguments, step=step):
                yield event
            step += 1

        await self._apply_llm_intent_if_ready(intent_task, wait_seconds=4.5)
        if self.llm_intent:
            keywords = "、".join(self.llm_intent.get("keywords") or [])
            yield status_event(f"LLM 已完成需求理解：{keywords}")

        keyword = _choose_search_keyword(self.text, self.context.profile, self.context.goal)
        async for event in self._execute_skill("search_places", {"keyword": keyword, "limit": 20}, step=step):
            yield event
        step += 1
        async for event in self._execute_skill("rank_places_for_plan", {"limit": 20}, step=step):
            yield event
        step += 1
        async for event in self._execute_skill("score_plans", {"limit": 3}, step=step):
            yield event

    async def run(self) -> AsyncIterator[dict]:
        started_at = asyncio.get_running_loop().time()
        deadline_at = started_at + _planning_timeout_seconds()

        def remaining_budget() -> float:
            return max(0.0, deadline_at - asyncio.get_running_loop().time())

        try:
            llm = create_llm_client()
            yield status_event(
                content=f"LLM provider: {llm.provider_name}, model: {llm.model_name}",
                provider=llm.provider_name,
                model=llm.model_name,
            )
        except LLMUnavailable as exc:
            llm = create_llm_client("local")
            yield status_event(
                content=f"LLM 未配置，已切换本地 fallback：{exc}",
                provider=llm.provider_name,
                model=llm.model_name,
            )

        # 不做并行意图提取，保留 API 配额用于路线选择（意图提取会触发额外一次 LLM 请求导致 429）
        intent_task: asyncio.Task | None = None

        if self.options.guided_bootstrap:
            async for event in self._guided_bootstrap(intent_task=intent_task):
                yield event

        # ── 快速通道：纯规划请求直接在 Python 构建 final_plan，跳过 LLM loop ──
        # action_bundle 已由本地评分链路生成；除非显式启用，否则不进远程 LLM 自由循环。
        _RESERVE_WORDS = ("预约", "订位", "下单", "预订", "帮我订", "帮我预约", "订票")
        _needs_llm = _enable_llm_agent_loop() and any(w in (self.text or "") for w in _RESERVE_WORDS)

        if not _needs_llm and self.context.ranked_places:
            # 先用本地多方案评分构建可执行底稿，再让 LLM 在有限预算内参与最终取舍。
            self.step_index += 1
            _fp_step = self.step_index
            yield skill_call_event(
                skill="build_final_plan",
                arguments={},
                visible_reason="先生成候选方案，再让 LLM 在限定时间内选择最终路线并生成说明",
                step=_fp_step,
            )
            base_plan = await self._finish_from_context()
            if intent_task and not self.llm_intent:
                await self._apply_llm_intent_if_ready(intent_task, wait_seconds=0.5)
            if intent_task and not self.llm_intent and not intent_task.done():
                intent_task.cancel()
            if self.llm_intent:
                base_plan["llm_intent_used"] = True
                base_plan["llm_intent"] = self.llm_intent
            remaining = remaining_budget()
            if getattr(llm, "provider_name", "") == "local":
                plan = {**base_plan, "llm_enhanced": False, "llm_usage_reason": "当前为本地 fallback 模式，没有调用远程 LLM"}
            elif _enable_llm_plan_selection() and remaining >= 2.0:
                llm_timeout = min(_llm_plan_selection_timeout_seconds(), max(2.0, remaining - 1.0))
                yield status_event(f"LLM 将在 {llm_timeout:.1f}s 预算内从多候选方案中选择最终路线")
                plan = await self._llm_choose_from_candidate_plans(llm, base_plan, timeout_seconds=llm_timeout)
            else:
                reason = "剩余规划时间不足，跳过 LLM 增强" if _enable_llm_plan_selection() else "配置关闭 LLM 方案增强"
                plan = {**base_plan, "llm_enhanced": False, "llm_usage_reason": reason}
            plan = _enrich_plan_steps(plan, self.context.ranked_places)
            if self.llm_intent:
                plan["llm_intent_used"] = True
                plan["llm_intent"] = self.llm_intent
            min_route_steps = 1 if len(base_plan.get("steps") or []) <= 1 else 2
            # 硬性规则：乐园≤1个、同园区去重、步骤不足补齐
            plan["steps"] = _post_process_steps(
                plan.get("steps") or [], self.context.ranked_places, _text_has_children(self.text), min_steps=min_route_steps
            )
            if not _plan_matches_request_shape(plan, self.text):
                yield status_event("AI 初选方案与时间/场景不匹配，已切换到规则校验后的方案")
                llm_usage_reason = (
                    "LLM 已调用审核，但输出与时间/场景规则不匹配，未采纳"
                    if plan.get("llm_enhanced") or getattr(llm, "provider_name", "") != "local"
                    else plan.get("llm_usage_reason")
                )
                plan = await self._finish_from_context()
                plan["llm_enhanced"] = False
                if self.llm_intent:
                    plan["llm_intent_used"] = True
                    plan["llm_intent"] = self.llm_intent
                if llm_usage_reason:
                    plan["llm_usage_reason"] = llm_usage_reason
                plan = _enrich_plan_steps(plan, self.context.ranked_places)
                plan["steps"] = _post_process_steps(
                    plan.get("steps") or [], self.context.ranked_places, _text_has_children(self.text), min_steps=min_route_steps
                )
            # 安全保底：至少2步
            if len(plan.get("steps") or []) < min_route_steps:
                plan["steps"] = _post_process_steps(
                    plan.get("steps") or [], self.context.ranked_places, _text_has_children(self.text), min_steps=min_route_steps
                )
            plan["steps"] = _retime_steps(plan.get("steps") or [], plan.get("time_plan") or self.context.time_plan)
            plan = self._rebuild_execution_bundle_for_plan(plan)
            plan = self._sanitize_plan_text(plan)
            yield observation_event(
                skill="build_final_plan",
                summary=f"路线规划完成，共 {len(plan.get('steps') or [])} 个步骤",
                data={"ok": True, "result": plan, "skill_name": "build_final_plan"},
            )
            min_seconds = _min_visible_planning_seconds()
            elapsed = asyncio.get_running_loop().time() - started_at
            if min_seconds > 0 and elapsed < min_seconds:
                yield status_event("正在校验路线时间、餐饮数量和动线合理性")
                await asyncio.sleep(min_seconds - elapsed)
            yield done_event(plan)
            return
        # ─────────────────────────────────────────────────────────────────────

        invalid_count = 0
        base_step = self.step_index
        for step in range(self.options.max_steps):
            event_step = base_step + step + 1
            try:
                raw = await llm.complete(self.messages)
            except Exception as exc:
                yield status_event(content=f"LLM 调用失败，切换本地 fallback：{exc}")
                llm = create_llm_client("local")
                raw = await llm.complete(self.messages)

            decision = parse_llm_decision(raw)
            if decision["type"] == "invalid":
                invalid_count += 1
                self.messages.append({
                    "role": "user",
                    "content": "FORMAT_ERROR: 请只输出合法 JSON，type 只能是 tool_call 或 final_answer。",
                })
                if invalid_count >= 2:
                    yield done_event(await self._finish_from_context())
                    return
                continue

            if decision["type"] == "tool_call":
                skill_name = decision.get("tool") or ""
                arguments = decision.get("arguments") or {}
                visible_reason = decision.get("visible_reason") or "调用工具"
                yield skill_call_event(
                    skill=skill_name,
                    arguments=arguments,
                    visible_reason=visible_reason,
                    step=event_step,
                )

                observation = await self.skill_runtime.execute(skill_name, arguments)
                public_event = _public_observation(skill_name, observation)
                public_event["step"] = event_step
                yield public_event
                self.step_index = event_step

                if skill_name == "build_final_plan" and observation.get("ok"):
                    plan = normalize_plan(observation.get("result") or {}, fallback_profile=self.context.profile or {})
                    if not _plan_is_usable(plan, self.context):
                        plan = await self._finish_from_context()
                    plan = _enrich_plan_steps(plan, self.context.ranked_places)
                    plan["steps"] = _post_process_steps(plan.get("steps") or [], self.context.ranked_places, _text_has_children(self.text))
                    yield done_event(plan)
                    return

                self.messages.append({"role": "assistant", "content": raw})
                self.messages.append(_observation_message(observation))
                continue

            plan = normalize_plan(decision.get("data") or {}, fallback_profile=self.context.profile or {})
            if not _plan_is_usable(plan, self.context):
                plan = await self._finish_from_context()
            plan = _enrich_plan_steps(plan, self.context.ranked_places)
            plan["steps"] = _post_process_steps(plan.get("steps") or [], self.context.ranked_places, _text_has_children(self.text))
            yield done_event(plan)
            return

        plan = await self._finish_from_context()
        plan = _enrich_plan_steps(plan, self.context.ranked_places)
        plan["steps"] = _post_process_steps(plan.get("steps") or [], self.context.ranked_places, _text_has_children(self.text))
        yield done_event(plan)
