# -*- coding: utf-8 -*-
"""Schema helpers and output normalization."""

from __future__ import annotations

import json
from typing import Any


def parse_json_object(text: str) -> dict | None:
    """Parse the first JSON object from model text, including fenced output."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def normalize_profile(profile: Any) -> dict:
    if not isinstance(profile, dict):
        profile = {}
    people = profile.get("people") if isinstance(profile.get("people"), dict) else {}
    return {
        "scene": profile.get("scene") if isinstance(profile.get("scene"), list) else [],
        "people": {
            "adults": int(people.get("adults") or 0),
            "children": int(people.get("children") or 0),
            "description": people.get("description") or "",
            "child_age": people.get("child_age") or "",
        },
        "preferences": profile.get("preferences") if isinstance(profile.get("preferences"), list) else [],
        "constraints": profile.get("constraints") if isinstance(profile.get("constraints"), list) else [],
        "city": profile.get("city") or "北京",
        "time_preference": profile.get("time_preference") or "未明确",
        "route_style": profile.get("route_style") or "均衡",
        "budget": profile.get("budget") if isinstance(profile.get("budget"), list) else [],
        "user_tags": profile.get("user_tags") if isinstance(profile.get("user_tags"), list) else [],
        "family_members": profile.get("family_members") if isinstance(profile.get("family_members"), list) else [],
        "raw_text": profile.get("raw_text") or "",
    }


def normalize_action(action: Any) -> dict:
    if not isinstance(action, dict):
        return {}
    return {
        key: value
        for key, value in action.items()
        if key in {"type", "target", "status", "message", "order_id", "time", "people_count", "price"}
    }


def normalize_step(step: Any) -> dict:
    if not isinstance(step, dict):
        return {}
    normalized = {
        "time": str(step.get("time") or ""),
        "date": str(step.get("date") or ""),   # 日期字段（"YYYY-MM-DD"）
        "name": str(step.get("name") or ""),
        "meta": str(step.get("meta") or ""),
        "reason": str(step.get("reason") or ""),
        "category": str(step.get("category") or ""),
        "lng": float(step.get("lng") or 0),
        "lat": float(step.get("lat") or 0),
    }
    for key in (
        "source",
        "source_id",
        "phone",
        "address",
        "open_hours",
        "price_range",
        "desc_text",
        "score",
        "capacity_status",
    ):
        if step.get(key) not in (None, ""):
            normalized[key] = str(step.get(key))
    if step.get("available_seats") is not None:
        normalized["available_seats"] = step.get("available_seats")
    return normalized


def normalize_plan(plan: Any, fallback_profile: dict | None = None) -> dict:
    """Guarantee the frontend plan shape."""
    if not isinstance(plan, dict):
        plan = {}
    raw_profile = plan.get("profile") if isinstance(plan.get("profile"), dict) else {}
    if isinstance(fallback_profile, dict):
        raw_profile = {**fallback_profile, **raw_profile}
    profile = normalize_profile(raw_profile)
    thinking = plan.get("thinking") if isinstance(plan.get("thinking"), list) else []
    steps = [normalize_step(step) for step in (plan.get("steps") or [])]
    actions = [normalize_action(action) for action in (plan.get("actions") or [])]
    normalized = {
        "type": "plan",
        "intro": plan.get("intro") or "我先根据你的需求生成了一份可执行的本地半日方案。",
        "profile": profile,
        "thinking": [str(item) for item in thinking if item][:5],
        "steps": [step for step in steps if step.get("name")],
        "actions": [action for action in actions if action.get("type")],
    }
    if isinstance(plan.get("current_time"), dict):
        normalized["current_time"] = plan["current_time"]
    if isinstance(plan.get("goal"), dict):
        normalized["goal"] = plan["goal"]
    if isinstance(plan.get("time_plan"), dict):
        normalized["time_plan"] = plan["time_plan"]
    if isinstance(plan.get("risks"), list):
        normalized["risks"] = [str(item) for item in plan["risks"] if item]
    if isinstance(plan.get("feasibility"), (bool, str)):
        normalized["feasibility"] = plan["feasibility"]
    if isinstance(plan.get("candidate_plans"), list):
        normalized["candidate_plans"] = plan["candidate_plans"]
    if isinstance(plan.get("selected_plan"), dict):
        normalized["selected_plan"] = plan["selected_plan"]
    if isinstance(plan.get("score_summary"), dict):
        normalized["score_summary"] = plan["score_summary"]
    if isinstance(plan.get("action_bundle"), dict):
        normalized["action_bundle"] = plan["action_bundle"]
    if isinstance(plan.get("llm_enhanced"), bool):
        normalized["llm_enhanced"] = plan["llm_enhanced"]
    return normalized


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
