# -*- coding: utf-8 -*-
"""Executable Python tools used by the planning agent.

Important boundary: this module never connects to the database and never imports
place.py. It only uses the places list passed in by run_agent/run_agent_stream.
"""

from __future__ import annotations

import math
import random
import re
import time
import hashlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .profile import build_user_profile


PLACE_FIELDS = (
    "name",
    "category",
    "keyword",
    "address",
    "open_hours",
    "price_range",
    "score",
    "desc_text",
    "lng",
    "lat",
)


WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _timezone(name: str | None = None) -> tuple[ZoneInfo, str]:
    requested = (name or "Asia/Beijing").strip() or "Asia/Beijing"
    zone_name = "Asia/Shanghai" if requested in {"Asia/Beijing", "Asia/Shanghai", "Beijing"} else requested
    try:
        return ZoneInfo(zone_name), requested
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai"), "Asia/Beijing"


def _period_for_hour(hour: int) -> str:
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 13:
        return "noon"
    if 13 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    return "night"


def get_current_time(timezone: str = "Asia/Beijing") -> dict:
    """Return server current time for planning. Defaults to Beijing time."""
    tz, label = _timezone(timezone)
    now = datetime.now(tz)
    return {
        "timezone": label,
        "now_iso": now.isoformat(timespec="minutes"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": WEEKDAYS_ZH[now.weekday()],
        "hour": now.hour,
        "minute": now.minute,
        "period": _period_for_hour(now.hour),
        "readable": f"{now.strftime('%Y-%m-%d')} {WEEKDAYS_ZH[now.weekday()]} {now.strftime('%H:%M')}",
    }


def _parse_people_from_text(text: str) -> dict:
    adults = 1
    children = 0
    total_match = re.search(r"([一二两三四五六七八九十\d]+)\s*个?人", text)
    if total_match:
        adults = max(adults, _cn_number(total_match.group(1), adults))
    gender_match = re.search(r"([一二两三四五六七八九十\d]+)\s*个?男(?:生|士)?\s*([一二两三四五六七八九十\d]+)\s*个?女", text)
    if gender_match:
        adults = max(adults, _cn_number(gender_match.group(1), 0) + _cn_number(gender_match.group(2), 0))
    if any(word in text for word in ["老婆", "妻子", "太太", "老公", "先生", "伴侣"]):
        adults += 1
    friend_match = re.search(r"([一二两三四五六七八九十\d]+)个?朋友", text)
    if friend_match:
        adults += _cn_number(friend_match.group(1), 1)
    elif "朋友" in text:
        adults += 1
    if any(word in text for word in ["孩子", "小孩", "儿童", "亲子", "宝宝"]):
        children = max(children, 1)
    return {
        "adults": adults,
        "children": children,
        "description": f"{adults}名成人" + (f"，{children}名儿童" if children else ""),
    }


def _cn_number(value: str, default: int = 0) -> int:
    value = str(value or "").strip()
    if value.isdigit():
        return int(value)
    mapping = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        return (mapping.get(left, 1) * 10) + mapping.get(right, 0)
    return mapping.get(value, default)


_CN_HOUR_MAP = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}


def _extract_time_window(text: str) -> dict:
    text = text or ""
    time_window = {
        "raw": "",
        "date_hint": "today",
        "period": "",
        "start_hint": "",
        "end_hint": "",
        "duration_minutes": 0,
        "confidence": 0.0,
    }

    # ── 日期提取（后天优先于明天）──────────────────────────────────
    weekday_match = re.search(r"(?:这周|本周|这星期|本星期|周|星期|礼拜)([一二三四五六日天])", text)
    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    if weekday_match:
        time_window["date_hint"] = "weekday"
        time_window["weekday"] = weekday_map.get(weekday_match.group(1), 5)
    elif "后天" in text or "大后天" in text:
        time_window["date_hint"] = "day_after_tomorrow"
    elif "明天" in text or "明晚" in text:
        time_window["date_hint"] = "tomorrow"
    elif "周末" in text:
        time_window["date_hint"] = "weekend"

    # ── 时段提取 ────────────────────────────────────────────────────
    date_label = {"tomorrow": "明天", "day_after_tomorrow": "后天", "weekend": "周末", "weekday": weekday_match.group(0) if weekday_match else ""}.get(time_window["date_hint"], "")
    if "现在" in text or "马上" in text:
        time_window.update({"period": "now", "start_hint": "now", "confidence": 0.95, "raw": "现在"})
    elif "一会儿" in text or "待会" in text or "等会" in text:
        time_window.update({"period": "soon", "start_hint": "soon", "confidence": 0.9, "raw": "一会儿"})
    elif any(w in text for w in ["全天", "一整天", "整天", "全日", "一天游", "全天游"]):
        time_window.update({"period": "full_day", "start_hint": "09:00", "end_hint": "21:00",
                             "confidence": 0.9, "raw": f"{date_label}全天"})
    elif "明晚" in text or "今晚" in text or "晚上" in text or "夜里" in text:
        time_window.update({"period": "evening", "start_hint": "18:00", "end_hint": "21:30",
                             "confidence": 0.85, "raw": f"{date_label}晚上"})
    elif "下午" in text:
        time_window.update({"period": "afternoon", "start_hint": "14:00", "end_hint": "18:00",
                             "confidence": 0.85, "raw": f"{date_label}下午"})
    elif "上午" in text:
        time_window.update({"period": "morning", "start_hint": "10:00", "end_hint": "12:00",
                             "confidence": 0.8, "raw": f"{date_label}上午"})
    elif "中午" in text:
        time_window.update({"period": "noon", "start_hint": "12:00", "end_hint": "13:30",
                             "confidence": 0.8, "raw": f"{date_label}中午"})

    # ── 具体时刻提取（覆盖 start_hint）─────────────────────────────
    hour: int | None = None
    minute: int = 0
    matched_raw: str = ""

    # 1) "3点" / "3:30" followed by "点" / "3点半" / "3点30分"
    m = re.search(r"(\d{1,2})(?:[:：](\d{2}))?\s*点(?:\s*(\d{1,2})\s*分?|\s*(半))?", text)
    if m:
        hour = int(m.group(1))
        if m.group(2):
            minute = int(m.group(2))
        elif m.group(3):
            minute = int(m.group(3))
        elif m.group(4):       # "半" = 30分
            minute = 30
        matched_raw = m.group(0)

    # 2) 中文数字"三点" / "三点半"（阿拉伯数字未匹配时才走这里）
    if hour is None:
        cn_pat = "|".join(re.escape(k) for k in sorted(_CN_HOUR_MAP, key=len, reverse=True))
        m2 = re.search(rf"({cn_pat})\s*点(?:\s*半)?", text)
        if m2:
            before = text[max(0, m2.start() - 3):m2.start()]
            ambiguous_little = m2.group(1) == "一" and not any(k in before for k in ["上午", "中午", "下午", "晚上"])
            if not ambiguous_little:
                hour = _CN_HOUR_MAP.get(m2.group(1), 0)
                minute = 30 if m2.group(0).endswith("半") else 0
                matched_raw = m2.group(0)

    # 3) "HH:MM" 纯数字格式（无"点"字，如"14:30"）
    if hour is None:
        m3 = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
        if m3:
            hour = int(m3.group(1))
            minute = int(m3.group(2))
            matched_raw = m3.group(0)

    if hour is not None:
        original_hour = hour
        # 12 小时 → 24 小时转换
        is_pm = "下午" in text or "今晚" in text or "晚上" in text or "夜里" in text
        is_am = "上午" in text
        if is_pm and 1 <= hour <= 11:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        time_window["start_hint"] = f"{hour:02d}:{minute:02d}"
        time_window["raw"] = f"{date_label}{matched_raw}" if date_label else matched_raw
        time_window["confidence"] = 0.95
        time_window["time_specified"] = True   # 标记用户给了明确时刻
        if not time_window["period"]:
            time_window["period"] = _period_for_hour(hour)
        range_match = re.search(r"(?:到|至|-|—|~)\s*(\d{1,2})(?:[:：](\d{2}))?\s*点?", text)
        if range_match:
            end_hour = int(range_match.group(1))
            end_minute = int(range_match.group(2) or 0)
            if is_pm and 1 <= end_hour <= 11:
                end_hour += 12
            elif is_am and end_hour == 12:
                end_hour = 0
            elif hour >= 12 and 1 <= end_hour <= 11 and end_hour <= original_hour:
                end_hour += 12
            end_hour = max(0, min(23, end_hour))
            end_minute = max(0, min(59, end_minute))
            end_hint = f"{end_hour:02d}:{end_minute:02d}"
            if end_hint != time_window["start_hint"]:
                time_window["end_hint"] = end_hint
        before_match = re.search(r"(?:晚上|晚)?\s*(\d{1,2})(?:[:：](\d{2}))?\s*点?(?:半)?\s*前(?:回家|结束|到家)?", text)
        if before_match:
            end_hour = int(before_match.group(1))
            end_minute = int(before_match.group(2) or (30 if "半" in before_match.group(0) else 0))
            if ("晚上" in before_match.group(0) or "晚" in before_match.group(0) or hour >= 12) and 1 <= end_hour <= 11:
                end_hour += 12
            end_hour = max(0, min(23, end_hour))
            end_minute = max(0, min(59, end_minute))
            end_hint = f"{end_hour:02d}:{end_minute:02d}"
            if end_hint != time_window["start_hint"]:
                time_window["end_hint"] = end_hint

    duration_minutes = _extract_duration_minutes(text)
    if duration_minutes:
        time_window["duration_minutes"] = duration_minutes
        time_window["confidence"] = max(float(time_window.get("confidence") or 0), 0.9)

    # ── raw 为空时设默认 ─────────────────────────────────────────────
    if not time_window["raw"]:
        if date_label:
            time_window["raw"] = date_label   # 只说了明天/后天，没有具体时段
        else:
            time_window.update({"raw": "未明确", "period": "unspecified", "confidence": 0.2})

    return time_window


def _extract_duration_minutes(text: str) -> int:
    text = text or ""
    if re.search(r"(\d+|一|二|两|三|四|五|六|七|八|九|十)\s*个?半小时", text):
        match = re.search(r"(\d+|一|二|两|三|四|五|六|七|八|九|十)\s*个?半小时", text)
        raw = match.group(1) if match else "1"
        hours = int(raw) if raw.isdigit() else _cn_number(raw, 1)
        return max(30, min(720, hours * 60 + 30))
    if any(word in text for word in ["半小时", "半个小时"]):
        return 30
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?小时", text)
    if match:
        return max(30, min(720, int(float(match.group(1)) * 60)))
    cn_pat = "|".join(re.escape(k) for k in sorted(_CN_HOUR_MAP, key=len, reverse=True))
    match = re.search(rf"({cn_pat})\s*(?:个)?小时", text)
    if match:
        hours = _CN_HOUR_MAP.get(match.group(1), 0)
        if hours:
            return max(30, min(720, hours * 60))
    if "半日" in text or "半天" in text:
        return 240
    if "一上午" in text:
        return 180
    if "一下午" in text:
        return 240
    if "一晚上" in text:
        return 210
    return 0


MEAL_INTENT_WORDS = ["吃", "餐厅", "饭馆", "晚饭", "晚餐", "午饭", "午餐", "早餐", "夜宵", "美食", "聚餐", "宴请", "火锅", "烤鸭"]
NO_MEAL_INTENT_WORDS = ["不吃饭", "不用吃饭", "不安排吃饭", "不要吃饭", "无需吃饭", "不吃东西", "不安排餐厅", "不要餐厅", "不考虑吃饭"]
ACTIVITY_INTENT_WORDS = ["玩", "逛", "散步", "看展", "景点", "公园", "亲子", "孩子", "小孩", "路线", "安排", "博物馆", "美术馆", "展览", "商场", "打发时间", "消磨时间", "随便逛逛"]
EXPLICIT_ACTIVITY_WORDS = ["玩", "逛", "散步", "看展", "景点", "公园", "亲子", "孩子", "小孩", "博物馆", "美术馆", "展览", "商场", "打发时间", "消磨时间", "随便逛逛"]
MULTI_PLACE_FOOD_WORDS = ["美食路线", "小吃路线", "探店路线", "吃吃逛逛", "美食半日", "美食一日", "美食一天"]


def _no_meal_request(text: str) -> bool:
    return any(word in str(text or "") for word in NO_MEAL_INTENT_WORDS)


def _implicit_lunch_request(text: str, time_window: dict | None = None) -> bool:
    text = str(text or "")
    time_window = time_window if isinstance(time_window, dict) else {}
    if _no_meal_request(text) or any(word in text for word in ["只安排上午", "上午就行", "到中午结束", "12点结束", "一上午"]):
        return False
    if any(word in text for word in ["午饭", "午餐", "中午吃", "吃饭"]):
        return True
    if _extract_duration_minutes(text):
        return False
    if any(word in text for word in ["老人", "父母", "爸妈", "爷爷", "奶奶", "长辈"]):
        return False
    start = _minute_from_clock(str(time_window.get("start_hint") or ""))
    morning_start = (time_window.get("period") == "morning") or (start is not None and 8 * 60 <= start <= 10 * 60)
    has_outing = any(word in text for word in ["约会", "情侣", "女朋友", "男朋友", "老婆", "老公"])
    return bool(morning_start and has_outing)


def _meal_only_request(text: str) -> bool:
    text = str(text or "")
    if _no_meal_request(text):
        return False
    has_meal = any(word in text for word in MEAL_INTENT_WORDS)
    if not has_meal:
        return False
    has_explicit_activity = any(word in text for word in EXPLICIT_ACTIVITY_WORDS)
    has_multi_food = any(word in text for word in MULTI_PLACE_FOOD_WORDS)
    has_long_window = any(word in text for word in ["一上午", "一下午", "一整天", "全天", "半日", "半天", "一天", "一日"])
    return not has_explicit_activity and not has_multi_food and not has_long_window


def decompose_goal(text: str, current_user: dict | None = None) -> dict:
    """Decompose the user request into planning slots and constraints."""
    text = (text or "").strip()
    time_window = _extract_time_window(text)
    task_slots: list[str] = []
    no_meal = _no_meal_request(text)
    if not no_meal and any(word in text for word in MEAL_INTENT_WORDS):
        task_slots.append("用餐")
    if not _meal_only_request(text) and any(word in text for word in ACTIVITY_INTENT_WORDS):
        task_slots.append("活动")
    if not no_meal and "用餐" not in task_slots and _implicit_lunch_request(text, time_window):
        task_slots.append("用餐")
    if not task_slots:
        task_slots.append("半日出行")

    hard_constraints: list[str] = []
    soft_constraints: list[str] = []
    if any(word in text for word in ["别太远", "不要太远", "近一点", "附近"]):
        hard_constraints.append("距离不要太远")
    if any(word in text for word in ["别太累", "不要太累", "不太累", "轻松一点", "别折腾"]):
        hard_constraints.append("行程不要太累")
    if any(word in text for word in ["孩子", "小孩", "亲子", "儿童"]):
        hard_constraints.append("亲子友好")
    if any(word in text for word in ["减肥", "清淡", "低脂", "低卡", "少油", "不吃辣", "不能吃辣", "不太能吃辣"]):
        soft_constraints.append("饮食低负担")
    if any(word in text for word in ["不吃辣", "不能吃辣", "不太能吃辣"]):
        hard_constraints.append("避免重辣")
    if any(word in text for word in ["室内", "下雨", "太晒", "太热", "太冷"]):
        soft_constraints.append("优先室内或低天气风险")

    return {
        "time_window": time_window,
        "participants": _parse_people_from_text(text),
        "task_slots": task_slots,
        "hard_constraints": hard_constraints,
        "soft_constraints": soft_constraints,
        "raw_text": text,
        "city": str((current_user or {}).get("city") or "北京"),
    }


def _datetime_from_clock(base: datetime, clock: str) -> datetime | None:
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(clock or ""))
    if not match:
        return None
    hour = max(0, min(int(match.group(1)), 23))
    minute = max(0, min(int(match.group(2)), 59))
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _round_up_minutes(value: datetime, step: int = 15) -> datetime:
    discard = value.minute % step
    if discard:
        value += timedelta(minutes=step - discard)
    return value.replace(second=0, microsecond=0)


def _parse_now(current_time: dict | None) -> datetime:
    current_time = current_time or get_current_time()
    tz, _ = _timezone(str(current_time.get("timezone") or "Asia/Beijing"))
    raw = current_time.get("now_iso")
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
        except Exception:
            pass
    return datetime.now(tz)


def plan_time_slots(text: str = "", current_time: dict | None = None, goal: dict | None = None) -> dict:
    """Create future time slots. Never returns step times earlier than now."""
    current_time = current_time or get_current_time()
    goal = goal or decompose_goal(text)
    now = _parse_now(current_time)
    warnings: list[str] = []
    time_window = goal.get("time_window") if isinstance(goal.get("time_window"), dict) else {}
    date_hint = time_window.get("date_hint") or "today"
    if date_hint == "weekday":
        target_weekday = int(_float(time_window.get("weekday"), 5))
        target_weekday = max(0, min(6, target_weekday))
        _day_offset = (target_weekday - now.weekday()) % 7
    elif date_hint == "weekend":
        _day_offset = 0 if now.weekday() in (5, 6) else max(0, 5 - now.weekday())
    else:
        _day_offset = {"tomorrow": 1, "day_after_tomorrow": 2}.get(date_hint, 0)
    base_date = now.date() + timedelta(days=_day_offset)
    base = datetime.combine(base_date, datetime.min.time(), tzinfo=now.tzinfo)

    period = time_window.get("period") or "unspecified"
    start_hint = str(time_window.get("start_hint") or "")
    end_hint = str(time_window.get("end_hint") or "")
    duration_minutes = int(_float(time_window.get("duration_minutes"), 0))

    if start_hint == "now":
        desired_start = _round_up_minutes(now + timedelta(minutes=10))
    elif start_hint == "soon":
        desired_start = _round_up_minutes(now + timedelta(minutes=30))
    elif start_hint:
        desired_start = _datetime_from_clock(base, start_hint) or _round_up_minutes(now + timedelta(minutes=45))
    elif period == "full_day":
        desired_start = base.replace(hour=9, minute=0)
        end_hint = end_hint or "21:00"
    elif period == "evening":
        desired_start = base.replace(hour=18, minute=0)
        end_hint = end_hint or "21:30"
    elif period == "afternoon":
        desired_start = base.replace(hour=14, minute=0)
        end_hint = end_hint or "18:00"
    elif period == "noon":
        desired_start = base.replace(hour=12, minute=0)
        end_hint = end_hint or "13:30"
    elif period == "morning":
        desired_start = base.replace(hour=10, minute=0)
        end_hint = end_hint or "12:00"
    elif _day_offset > 0:
        # 明天/后天但未指定时段，默认上午10点出发
        desired_start = base.replace(hour=10, minute=0)
        end_hint = end_hint or "18:00"
    else:
        desired_start = _round_up_minutes(now + timedelta(minutes=45))

    min_start = _round_up_minutes(now + timedelta(minutes=30))
    adjusted = False
    time_specified = time_window.get("time_specified", False)

    if desired_start <= now:
        if time_specified and _day_offset == 0:
            # 用户给了明确时刻（如"下午三点"）但今天已经过了
            # → 顺延到明天同一时间，而不是改成"现在"
            next_base = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)
            next_start = _datetime_from_clock(next_base, start_hint)
            if next_start and next_start > now:
                desired_start = next_start
                desired_end = _datetime_from_clock(next_base, end_hint) if end_hint else None
                if not desired_end or desired_end <= desired_start:
                    desired_end = desired_start + timedelta(hours=4)
                desired_end = max(desired_end, desired_start + timedelta(hours=4))
                warnings.append(f"今天该时间已过，已自动顺延到明天 {start_hint}。")
            else:
                desired_start = min_start
                adjusted = True
        else:
            desired_start = min_start
            adjusted = True
    elif desired_start < min_start and _day_offset == 0:
        desired_start = min_start
        adjusted = True

    if adjusted:
        warnings.append("用户给出的时间已经过去或距离当前过近，已自动调整到当前时间之后。")

    if duration_minutes:
        desired_end = desired_start + timedelta(minutes=duration_minutes)
    else:
        desired_end = _datetime_from_clock(base, end_hint) if end_hint else None
        if _implicit_lunch_request(str(goal.get("raw_text") or text or ""), time_window):
            lunch_end = desired_start.replace(hour=14, minute=0)
            if lunch_end > desired_start and (not desired_end or desired_end < lunch_end):
                desired_end = lunch_end
        if desired_end and desired_end <= desired_start:
            if adjusted and end_hint:
                fallback_minutes = {
                    "morning": 120,
                    "noon": 90,
                    "afternoon": 240,
                    "evening": 210,
                }.get(period, 240)
                desired_end = desired_start + timedelta(minutes=fallback_minutes)
            else:
                desired_end += timedelta(days=1)
        if not desired_end:
            desired_end = desired_start + timedelta(hours=4)
        elif (desired_end - desired_start).total_seconds() < 45 * 60:
            desired_end = desired_start + timedelta(minutes=45)

    task_slots = goal.get("task_slots") if isinstance(goal.get("task_slots"), list) else []
    slot_names = [str(item) for item in task_slots if item] or ["活动"]
    only_meal = "用餐" in slot_names and "活动" not in slot_names
    total_minutes = int((desired_end - desired_start).total_seconds() / 60)
    if "活动" in slot_names and "用餐" in slot_names:
        labels = ["活动", "用餐"] if total_minutes <= 180 else ["活动", "活动", "用餐"]
    elif only_meal:
        labels = ["用餐"]
    else:
        labels = ["活动"] if total_minutes <= 90 else (["活动", "休整"] if total_minutes <= 150 else ["活动", "活动", "休整", "收尾"])

    # 长时间窗口（≥6小时）扩展 label，最多5个步骤
    if total_minutes >= 360 and len(labels) <= 4:
        if "用餐" in labels:
            labels = ["活动", "活动", "用餐", "活动", "收尾"]
        else:
            labels = ["活动", "活动", "活动", "休整", "收尾"]

    slots = []
    cursor = desired_start
    for index, label in enumerate(labels[:5]):
        duration_minutes = 90 if index == 0 else 60
        slot_end = min(cursor + timedelta(minutes=duration_minutes), desired_end)
        if slot_end <= cursor:
            break
        slots.append({
            "index": index + 1,
            "label": label,
            "start": cursor.strftime("%H:%M"),
            "end": slot_end.strftime("%H:%M"),
            "date": cursor.strftime("%Y-%m-%d"),
        })
        cursor = slot_end
        if cursor >= desired_end:
            break

    return {
        "source": "plan_time_slots",
        "start": desired_start.strftime("%H:%M"),
        "end": desired_end.strftime("%H:%M"),
        "date": desired_start.strftime("%Y-%m-%d"),
        "period": _period_for_hour(desired_start.hour),
        "timezone": current_time.get("timezone") or "Asia/Beijing",
        "slots": slots,
        "warnings": warnings,
    }


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_place(place: Any) -> dict | None:
    """Normalize a place row from MySQL dicts or local PLACES tuples."""
    if isinstance(place, dict):
        name = place.get("name")
        if not name:
            return None
        return {
            "name": str(name),
            "category": str(place.get("category", "")),
            "keyword": str(place.get("keyword", "") or place.get("tags", "")),
            "address": str(place.get("address", "")),
            "open_hours": str(place.get("open_hours", "")),
            "price_range": str(place.get("price_range", "") or place.get("price", "")),
            "score": str(place.get("score", "")),
            "desc_text": str(place.get("desc_text", "") or place.get("desc", "")),
            "lng": _float(place.get("lng"), 0.0),
            "lat": _float(place.get("lat"), 0.0),
            "available_seats": place.get("available_seats"),
            "queue_count": place.get("queue_count"),
            "capacity_status": place.get("capacity_status"),
            "source": str(place.get("source", "")),
            "source_id": str(place.get("source_id", "")),
            "phone": str(place.get("phone", "")),
            "reason": str(place.get("reason", "")),
            "final_score": place.get("final_score"),
            "score_breakdown": place.get("score_breakdown") if isinstance(place.get("score_breakdown"), dict) else {},
            "risks": place.get("risks") if isinstance(place.get("risks"), list) else [],
        }

    if isinstance(place, (tuple, list)) and len(place) >= len(PLACE_FIELDS):
        data = dict(zip(PLACE_FIELDS, place[: len(PLACE_FIELDS)]))
        return _normalize_place(data)

    return None


def normalize_places(places: list | None) -> list[dict]:
    """Return only externally provided, valid places. No built-in fallback."""
    if not places:
        return []
    normalized: list[dict] = []
    seen: set[str] = set()
    for place in places:
        item = _normalize_place(place)
        if not item or item["name"] in seen:
            continue
        normalized.append(item)
        seen.add(item["name"])
    return normalized


def valid_place_names(places: list | None) -> set[str]:
    return {place["name"] for place in normalize_places(places)}


def _as_text(place: dict) -> str:
    fields = [
        place.get("name", ""),
        place.get("category", ""),
        place.get("keyword", ""),
        place.get("address", ""),
        place.get("open_hours", ""),
        place.get("price_range", ""),
        place.get("desc_text", ""),
    ]
    return " ".join(str(x) for x in fields if x)


def _contains_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def analyze_user_profile(
    text: str,
    current_user: dict | None = None,
    family_members: list | None = None,
    preference_memory: list | None = None,
) -> dict:
    return build_user_profile(text, current_user=current_user, family_members=family_members, preference_memory=preference_memory)


def _expand_keywords(keyword: str) -> list[str]:
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    expansions = {
        "亲子": ["亲子", "孩子", "儿童", "小孩", "宝宝", "游乐", "科普", "动物", "海洋", "公园", "博物馆", "餐厅", "海鲜", "清淡", "轻食", "咖啡"],
        "恐龙": ["恐龙", "化石", "古生物", "自然博物馆", "自然", "科普", "博物馆", "亲子", "儿童", "公园", "清淡", "轻食", "餐厅"],
        "孩子": ["亲子", "孩子", "儿童", "游乐", "科普", "动物", "海洋", "公园"],
        "朋友": ["朋友", "聚会", "聚餐", "多人", "咖啡", "餐厅", "美食", "酒吧"],
        "聚会": ["朋友", "聚会", "聚餐", "多人", "餐厅", "咖啡"],
        "减肥": ["轻食", "清淡", "低卡", "低糖", "蒸汽", "海鲜", "公园", "散步"],
        "低负担": ["轻食", "清淡", "蒸汽", "海鲜", "公园", "散步"],
        "休闲": ["休闲", "散步", "公园", "咖啡", "胡同", "艺术", "游船"],
        "美食": ["美食", "餐厅", "聚餐", "海鲜", "火锅", "烤鸭", "小吃", "咖啡"],
        "火锅": ["火锅", "涮肉", "清汤", "鸳鸯锅", "不辣", "聚餐", "餐厅", "拍照", "打卡"],
        "清淡": ["清淡", "轻食", "低卡", "素食", "海鲜", "汤", "粥", "餐厅", "茶"],
        "约会": ["约会", "咖啡", "酒吧", "餐厅", "散步", "浪漫", "景观"],
        "商务": ["商务", "宴请", "餐厅", "高端", "米其林", "正式"],
        "拍照": ["拍照", "打卡", "出片", "网红", "艺术", "胡同", "景观", "咖啡"],
        "室内": ["室内", "博物馆", "美术馆", "商场", "影院", "咖啡", "餐厅", "展览"],
        "自然": ["自然", "公园", "散步", "户外", "游船", "湖", "花园", "绿地", "餐厅", "咖啡"],
        "文化": ["文化", "博物馆", "美术馆", "艺术", "展览", "历史", "胡同", "书店", "餐厅", "咖啡"],
        "省钱": ["免费", "公园", "博物馆", "散步", "小吃", "咖啡", "平价"],
        "高端": ["高端", "品质", "商务", "宴请", "酒店", "米其林", "精品", "酒吧"],
    }
    words = expansions.get(keyword, [])
    return [keyword, *[word for word in words if word != keyword]]


def search_places(keyword: str = "", limit: int = 8, places: list | None = None) -> dict:
    """Search only the passed-in places list. No database and no built-in places."""
    pool = normalize_places(places)
    limit = max(1, min(int(limit or 8), 20))
    keyword = (keyword or "").strip()
    words = _expand_keywords(keyword)

    scored: list[tuple[int, dict]] = []
    for place in pool:
        haystack = _as_text(place)
        score = int(_float(place.get("score")) * 2)
        for word in words:
            if word and word in haystack:
                score += 12 if word == keyword else 8
        if not keyword or score > int(_float(place.get("score")) * 2):
            scored.append((score, place))

    if keyword and not scored:
        # Still use real places only: when keyword matching is too strict, return
        # top-rated rows so the next ranking tool can recover.
        scored = [(int(_float(place.get("score")) * 2), place) for place in pool]

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[dict] = []
    group_counts: dict[str, int] = {}
    for _, place in scored:
        group = _category_group(place)
        group_limit = 6 if group in {"playground", "food"} else limit
        if group_counts.get(group, 0) >= group_limit:
            continue
        selected.append(place)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        used = {place.get("name") for place in selected}
        for _, place in scored:
            if place.get("name") in used:
                continue
            selected.append(place)
            if len(selected) >= limit:
                break
    if keyword in {"美食", "餐厅", "火锅", "烤鸭", "海鲜"} and len(selected) < limit:
        used = {place.get("name") for place in selected}
        fillers = [place for place in pool if place.get("name") not in used and not _category_is_food(place)]
        fillers.sort(key=lambda place: _float(place.get("score")), reverse=True)
        for place in fillers:
            selected.append(place)
            used.add(place.get("name"))
            if len(selected) >= limit:
                break
    elif keyword in {"美食", "餐厅", "火锅", "烤鸭", "海鲜"}:
        non_food_count = sum(1 for place in selected if not _category_is_food(place))
        if non_food_count < 3:
            used = {place.get("name") for place in selected}
            fillers = [place for place in pool if place.get("name") not in used and not _category_is_food(place)]
            fillers.sort(key=lambda place: _float(place.get("score")), reverse=True)
            replace_index = len(selected) - 1
            for place in fillers[: 3 - non_food_count]:
                while replace_index >= 0 and not _category_is_food(selected[replace_index]):
                    replace_index -= 1
                if replace_index < 0:
                    break
                selected[replace_index] = place
                replace_index -= 1
    elif keyword in {"文化", "自然", "休闲", "室内", "约会"}:
        non_food_count = sum(1 for place in selected if not _category_is_food(place))
        if non_food_count < 6:
            used = {place.get("name") for place in selected}
            fillers = [place for place in pool if place.get("name") not in used and not _category_is_food(place)]
            fillers.sort(key=lambda place: _float(place.get("score")), reverse=True)
            replace_index = len(selected) - 1
            for place in fillers[: 6 - non_food_count]:
                while replace_index >= 0 and not _category_is_food(selected[replace_index]):
                    replace_index -= 1
                if replace_index < 0:
                    break
                selected[replace_index] = place
                replace_index -= 1
        food_count = sum(1 for place in selected if _category_is_food(place))
        if food_count < 3:
            used = {place.get("name") for place in selected}
            fillers = [place for place in pool if place.get("name") not in used and _category_is_food(place)]
            fillers.sort(key=lambda place: _float(place.get("score")), reverse=True)
            replace_index = len(selected) - 1
            for place in fillers[: 3 - food_count]:
                while replace_index >= 0 and _category_is_food(selected[replace_index]):
                    replace_index -= 1
                if replace_index < 0:
                    break
                selected[replace_index] = place
                replace_index -= 1
    return {
        "places": selected[:limit],
        "keyword": keyword,
        "count": min(len(scored), limit),
        "source": "passed_in_places",
    }


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Approximate straight-line distance in km between two WGS84 points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _nearby_bonus(place: dict, places: list[dict]) -> int:
    lng = _float(place.get("lng"))
    lat = _float(place.get("lat"))
    distances = [
        math.hypot(lng - _float(other.get("lng")), lat - _float(other.get("lat")))
        for other in places
        if other.get("name") != place.get("name")
    ]
    if not distances:
        return 0
    nearest = min(distances)
    if nearest <= 0.01:
        return 8
    if nearest <= 0.03:
        return 4
    return 0


def _covers_afternoon(open_hours: str) -> bool:
    if "全天" in open_hours or "24" in open_hours:
        return True
    numbers = [int(n) for n in re.findall(r"\d{1,2}", open_hours)]
    return any(n >= 17 for n in numbers)


def _minute_from_clock(clock: str | None) -> int | None:
    if not clock:
        return None
    cleaned = str(clock).strip().replace("：", ":")
    match = re.match(r"^(\d{1,2})(?::(\d{1,2}))?$", cleaned)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour == 24:
        hour = 23
        minute = 59
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def _parse_open_hours_ranges(open_hours: str) -> list[tuple[int, int]]:
    text = (open_hours or "").strip()
    if not text:
        return []
    if any(word in text for word in ["全天", "24小时", "24h", "24H", "24 时"]):
        return [(0, 24 * 60 - 1)]

    ranges: list[tuple[int, int]] = []
    for part in re.split(r"[，,；;/、]+", text):
        part = part.strip()
        if not part:
            continue
        if "至" in part:
            bounds = [p.strip() for p in part.split("至", 1)]
        elif "-" in part:
            bounds = [p.strip() for p in part.split("-", 1)]
        else:
            bounds = re.findall(r"(\d{1,2}(?::\d{1,2})?)", part)
        if len(bounds) >= 2:
            start = _minute_from_clock(bounds[0])
            end = _minute_from_clock(bounds[1])
            if start is not None and end is not None and end > start:
                ranges.append((start, end))
    return ranges


def _open_hours_confidence(open_hours: str) -> tuple[int, list[str]]:
    if not open_hours or not str(open_hours).strip():
        return 0, ["营业时间不明确，建议出发前再确认。"]
    ranges = _parse_open_hours_ranges(str(open_hours))
    if not ranges:
        return 0, ["营业时间描述不明确或解析失败，无法确认到店时间是否可用。"]
    return 10, []


def _desired_visit_minute(profile: dict, current_time: dict | None, time_plan: dict | None) -> int:
    if isinstance(time_plan, dict) and isinstance(time_plan.get("start"), str):
        minute = _minute_from_clock(time_plan.get("start"))
        if minute is not None:
            return minute
    text = str(profile.get("time_preference") or "")
    match = re.search(r"(\d{1,2})(?:[:：](\d{1,2}))?\s*点", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if "下午" in text and hour < 12:
            hour += 12
        return _minute_from_clock(f"{hour:02d}:{minute:02d}") or 14 * 60
    now = _parse_now(current_time)
    return now.hour * 60 + now.minute


def _score_place_time_fit(place: dict, profile: dict, current_time: dict | None, time_plan: dict | None) -> tuple[int, list[str], int]:
    open_hours = str(place.get("open_hours") or "")
    desired = _desired_visit_minute(profile, current_time, time_plan)
    ranges = _parse_open_hours_ranges(open_hours)
    notes: list[str] = []
    if not ranges:
        return 2, notes, -8

    for start, end in ranges:
        if start <= desired < end:
            notes.append("营业时间覆盖预计出发时间")
            return 18, notes, 0
        if start - 60 <= desired < end + 60:
            notes.append("营业时间与预计出发时间较接近")
            return 10, notes, 0

    notes.append("营业时间可能不覆盖预计出发时间，需要提前确认")
    return 2, notes, -12


def _score_place_group_fit(place: dict, profile: dict) -> int:
    text = _as_text(place)
    scene = profile.get("scene") or []
    people = profile.get("people") or {}
    score = 0
    if any(word in text for word in ["亲子", "孩子", "儿童", "小孩", "游乐", "动物", "海洋", "科普", "公园", "博物馆"]):
        score += 15
    if any(word in text for word in ["聚餐", "多人", "朋友", "团体", "宴会", "咖啡", "餐厅"]):
        score += 12
    if any(word in text for word in ["约会", "浪漫", "庭院", "景观"]):
        score += 10
    if (people.get("children") or 0) > 0 and any(word in text for word in ["亲子", "儿童", "小孩"]):
        score += 10
    if "亲子出行" in scene and any(word in text for word in ["亲子", "儿童", "小孩"]):
        score += 10
    return min(score, 25)


def _explicit_interest_words(text: str) -> list[str]:
    text = text or ""
    interests: list[str] = []
    if any(word in text for word in ["恐龙", "化石", "古生物"]):
        interests.extend(["恐龙", "自然博物馆", "化石", "古生物"])
    return interests


def _place_matches_interest(place: dict, words: list[str]) -> bool:
    if not words:
        return False
    text = _as_text(place)
    return any(word and word in text for word in words)


def _interest_match_score(place: dict, words: list[str]) -> int:
    if not words:
        return 0
    text = _as_text(place)
    score = 0
    for word in words:
        if word and word in text:
            score += 28 if word in {"恐龙", "自然博物馆"} else 18
    return min(score, 36)


def _score_place_diet_fit(place: dict, profile: dict) -> int:
    text = _as_text(place)
    constraints = profile.get("constraints") or []
    preferences = profile.get("preferences") or []
    active_diet = any(word in "".join(constraints + preferences) for word in ["低负担", "低油", "低卡", "清淡", "少油", "减肥"])
    avoid_spicy = any(word in "".join(constraints + preferences) for word in ["避免重辣", "不吃辣", "不能吃辣"])
    if not active_diet and not avoid_spicy:
        return 6
    if avoid_spicy and any(word in text for word in ["麻辣", "川味", "辣", "湘菜"]):
        return 1
    if avoid_spicy and any(word in text for word in ["清汤", "鸳鸯锅", "涮肉", "潮汕", "粤菜", "清淡"]):
        return 16
    if any(word in text for word in ["清淡", "轻食", "沙拉", "素食", "海鲜", "汤", "蔬菜", "咖啡"]):
        return 16
    if any(word in text for word in ["火锅", "烧烤", "烤肉", "麻辣", "炸", "油炸", "夜宵"]):
        return 2
    return 10


def _score_place_weather_fit(place: dict, profile: dict) -> int:
    text = _as_text(place)
    constraints = profile.get("constraints") or []
    preferences = profile.get("preferences") or []
    weather_sensitive = any(word in "".join(constraints + preferences) for word in ["室内", "下雨", "雨", "太晒", "太热", "太冷", "风大"])
    if not weather_sensitive:
        return 6
    if any(word in text for word in ["室内", "商场", "博物馆", "影院", "咖啡", "餐厅", "酒店"]):
        return 16
    if any(word in text for word in ["公园", "露天", "景区", "步行", "游船"]):
        return 8
    return 4


def _score_place_style_fit(place: dict, profile: dict) -> int:
    style = str(profile.get("route_style") or "均衡")
    text = _as_text(place)
    category = str(place.get("category") or "")
    if style == "均衡":
        return 6

    style_words = {
        "亲子": ["亲子", "儿童", "孩子", "小孩", "游乐", "科普", "动物", "海洋", "博物馆"],
        "美食": ["餐厅", "美食", "海鲜", "火锅", "烤鸭", "小吃", "咖啡", "酒吧"],
        "约会": ["约会", "浪漫", "咖啡", "酒吧", "景观", "艺术", "拍照", "出片"],
        "朋友": ["聚会", "多人", "聊天", "咖啡", "餐厅", "酒吧", "互动"],
        "商务": ["商务", "宴请", "高端", "正式", "酒店", "服务", "私密"],
        "拍照": ["拍照", "打卡", "出片", "网红", "艺术", "胡同", "景观"],
        "室内": ["室内", "博物馆", "美术馆", "影院", "商场", "咖啡", "餐厅", "展览"],
        "自然": ["公园", "自然", "散步", "户外", "游船", "湖", "绿地", "花园"],
        "文化": ["博物馆", "美术馆", "文化", "艺术", "展览", "历史", "胡同", "书店"],
        "省钱": ["免费", "公园", "博物馆", "散步", "小吃", "平价"],
        "高端": ["高端", "品质", "商务", "宴请", "酒店", "精品", "酒吧"],
        "轻松": ["咖啡", "公园", "散步", "休闲", "书店", "轻食", "艺术"],
    }
    words = style_words.get(style, [])
    if any(word in text for word in words):
        return 22
    if style == "美食" and category in {"餐厅", "咖啡", "酒吧"}:
        return 22
    if style == "室内" and category in {"餐厅", "咖啡", "景点"} and any(word in text for word in ["馆", "室内", "展"]):
        return 18
    return 3


def _score_place_distance_fit(place: dict, profile: dict, pool: list[dict]) -> int:
    user_lat = profile.get("user_lat")
    user_lng = profile.get("user_lng")
    has_constraint = "距离不要太远" in (profile.get("constraints") or [])

    if user_lat is not None and user_lng is not None:
        place_lat = _float(place.get("lat"))
        place_lng = _float(place.get("lng"))
        if place_lat and place_lng:
            km = _haversine_km(float(user_lat), float(user_lng), place_lat, place_lng)
            if km <= 3:
                return 18 if has_constraint else 14
            elif km <= 8:
                return 12 if has_constraint else 10
            elif km <= 20:
                return 6 if has_constraint else 8
            else:
                return 0 if has_constraint else 4

    # 无用户定位时兜底：按候选地点互相聚集度评分
    if not has_constraint:
        return 6
    bonus = _nearby_bonus(place, pool)
    if bonus >= 8:
        return 16
    if bonus >= 4:
        return 12
    if bonus > 0:
        return 8
    return 2


def _score_place_execution_fit(place: dict, open_hours: str) -> int:
    score = 0
    parsed = _parse_open_hours_ranges(open_hours)
    if parsed:
        score += 8
    if _float(place.get("score")) >= 4.0:
        score += 6
    elif _float(place.get("score")) >= 3.5:
        score += 4
    else:
        score += 2
    return min(score, 14)


def _score_place_capacity_fit(place: dict, profile: dict) -> tuple[int, list[str], int]:
    people = _people_count(profile)
    available = place.get("available_seats")
    status = str(place.get("capacity_status") or "").strip().lower()
    notes: list[str] = []
    penalty = 0

    if available is not None:
        available_num = int(_float(available, 0))
        if available_num <= 0:
            notes.append("当前可用座位未知或为0，预约可行性不确定")
            penalty -= 8
        elif people > available_num:
            notes.append(f"可用座位不足：仅剩{available_num}席，超出同行人数")
            penalty -= 18
        elif people + 2 >= available_num:
            notes.append("座位紧张，预约风险较高")
            penalty -= 8
        else:
            notes.append("座位充足，可预约成功概率高")
            return 8, notes, 0

    if status and status not in {"available", "open", "normal", "ok"}:
        notes.append(f"当前预约状态为{status}，可能无法预约")
        penalty -= 12

    if not notes:
        notes.append("预约可行性暂时正常")
    return 4, notes, penalty


def _place_score_breakdown(place: dict, profile: dict, current_time: dict | None, time_plan: dict | None, pool: list[dict]) -> tuple[dict, list[str]]:
    profile_match = 0
    text = _as_text(place)
    scene = profile.get("scene") or []
    if "亲子出行" in scene or "亲子" in text:
        profile_match += 12
    if "朋友聚会" in scene:
        profile_match += 10
    if "用餐" in scene and _category_is_food(place):
        profile_match += 10
    if any(word in text for word in ["清淡", "轻食", "低卡", "素食", "海鲜"]):
        profile_match += 8
    if any(word in text for word in ["公园", "景点", "散步", "游船", "文化"]):
        profile_match += 6
    profile_match = min(profile_match, 20)

    time_fit, time_notes, time_penalty = _score_place_time_fit(place, profile, current_time, time_plan)
    distance_fit = _score_place_distance_fit(place, profile, pool)
    diet_fit = _score_place_diet_fit(place, profile)
    weather_fit = _score_place_weather_fit(place, profile)
    style_fit = _score_place_style_fit(place, profile)
    group_fit = _score_place_group_fit(place, profile)
    execution_fit = _score_place_execution_fit(place, str(place.get("open_hours") or ""))
    capacity_fit, capacity_notes, capacity_penalty = _score_place_capacity_fit(place, profile)
    price_fit = _score_place_price_fit(place, profile)
    risk_penalty = time_penalty + capacity_penalty
    risks = []
    if time_penalty < 0 and time_notes:
        risks.extend(time_notes)
    if capacity_penalty < 0 and capacity_notes:
        risks.extend(capacity_notes)
    if _open_hours_confidence(str(place.get("open_hours") or ""))[0] == 0:
        risks.append("该地点营业时间描述不明确，可能需要额外确认。")

    total_score = 35 + profile_match + time_fit + distance_fit + diet_fit + weather_fit + style_fit + group_fit + execution_fit + capacity_fit + price_fit + risk_penalty
    final_score = max(0, min(100, int(total_score)))

    breakdown = {
        "profile_match": profile_match,
        "time_fit": time_fit,
        "distance_fit": distance_fit,
        "diet_fit": diet_fit,
        "weather_fit": weather_fit,
        "style_fit": style_fit,
        "group_fit": group_fit,
        "execution_fit": execution_fit,
        "price_fit": price_fit,
        "risk_penalty": risk_penalty,
    }
    return breakdown, list(dict.fromkeys(risks))


def _normalize_candidate_steps(combo: list[dict], profile: dict, time_plan: dict | None) -> list[dict]:
    slots = time_plan.get("slots") if isinstance(time_plan.get("slots"), list) else []
    steps: list[dict] = []
    for index, place in enumerate(combo):
        slot = slots[index] if index < len(slots) and isinstance(slots[index], dict) else None
        current = str(slot.get("start")) if slot else _next_time(time_plan.get("start") if isinstance(time_plan.get("start"), str) else _start_time(profile), index)
        desc = (place.get("desc_text") or "").strip()
        price = (place.get("price_range") or "").strip()
        meta_parts = [p for p in [desc, price] if p]
        meta = "；".join(meta_parts) or (place.get("reason") or "匹配当前需求")
        step_date = slot.get("date") if slot else (time_plan.get("date") or "")
        steps.append({
            "index": index + 1,
            "time": current,
            "date": step_date,
            "name": place["name"],
            "category": place.get("category"),
            "meta": meta,
            "reason": str(place.get("reason") or ""),
            "lng": place.get("lng", 0.0),
            "lat": place.get("lat", 0.0),
            "score": place.get("score"),
            "source": place.get("source", ""),
            "source_id": place.get("source_id", ""),
            "phone": place.get("phone", ""),
            "address": place.get("address", ""),
            "open_hours": place.get("open_hours", ""),
            "price_range": place.get("price_range", ""),
            "desc_text": place.get("desc_text", ""),
            "available_seats": place.get("available_seats"),
            "capacity_status": place.get("capacity_status"),
            "score_breakdown": place.get("score_breakdown") or {},
        })
    return steps


def _place_reservation_blocked(place: dict, people_count: int) -> str:
    available = place.get("available_seats")
    if available is not None and people_count > int(_float(available, 0)):
        return f"仅剩{int(_float(available, 0))}席"
    status = str(place.get("capacity_status") or "").strip().lower()
    if status and status not in {"available", "open", "normal", "ok"}:
        return f"状态为{status}"
    return ""


def _plan_score_from_combo(combo: list[dict]) -> tuple[int, dict, int]:
    if not combo:
        return 0, {}, 0
    breakdown = {
        "profile_match": 0,
        "time_fit": 0,
        "distance_fit": 0,
        "diet_fit": 0,
        "weather_fit": 0,
        "style_fit": 0,
        "group_fit": 0,
        "execution_fit": 0,
        "price_fit": 0,
        "risk_penalty": 0,
    }
    count = 0
    for place in combo:
        place_breakdown = place.get("score_breakdown") or {}
        if not isinstance(place_breakdown, dict):
            continue
        for key in breakdown:
            if key in place_breakdown:
                breakdown[key] += int(place_breakdown[key] or 0)
        count += 1
    if count:
        for key in [k for k in breakdown if k != "risk_penalty"]:
            breakdown[key] = int(breakdown[key] / count)
    risk_penalty = int(breakdown.get("risk_penalty") or 0)
    raw_score = 15 + breakdown["profile_match"] + breakdown["time_fit"] + breakdown["distance_fit"] + breakdown["diet_fit"] + breakdown["weather_fit"] + breakdown["style_fit"] + breakdown["group_fit"] + breakdown["execution_fit"] + breakdown["price_fit"] + risk_penalty
    final_score = max(0, min(100, int(raw_score)))
    return final_score, breakdown, risk_penalty


def _place_matches(place: dict, words: list[str]) -> bool:
    text = _as_text(place)
    return any(word in text for word in words)


def _first_not_used(candidates: list[dict], used: set[str]) -> dict | None:
    return next((place for place in candidates if place.get("name") not in used), None)


def _combo_from_groups(*groups: list[dict], max_len: int = 3) -> list[dict]:
    combo: list[dict] = []
    used: set[str] = set()
    for group in groups:
        place = _first_not_used(group, used)
        if not place:
            continue
        combo.append(place)
        used.add(place["name"])
        if len(combo) >= max_len:
            break
    return combo


def _style_plan_combos(ranked: list[dict], profile: dict) -> list[list[dict]]:
    style = str(profile.get("route_style") or "均衡")
    activities = [p for p in ranked if not _category_is_food(p)]
    foods = [p for p in ranked if _category_is_food(p)]
    parks = [p for p in ranked if _place_matches(p, ["公园", "自然", "散步", "户外", "游船", "湖"])]
    indoor = [p for p in ranked if _place_matches(p, ["室内", "博物馆", "美术馆", "影院", "商场", "展览", "馆"])]
    culture = [p for p in ranked if _place_matches(p, ["博物馆", "美术馆", "文化", "艺术", "展览", "历史", "胡同", "书店"])]
    photo = [p for p in ranked if _place_matches(p, ["拍照", "打卡", "出片", "网红", "艺术", "胡同", "景观"])]
    cafes = [p for p in ranked if _place_matches(p, ["咖啡", "茶", "甜品"])]
    free_or_low = [p for p in ranked if _place_matches(p, ["免费", "公园", "博物馆", "小吃", "平价"])]
    high_end = [p for p in ranked if _place_matches(p, ["高端", "品质", "商务", "宴请", "酒店", "精品", "酒吧"])]
    playgrounds = [p for p in ranked if _is_playground(p)]

    if style == "亲子":
        return [
            _combo_from_groups(playgrounds, parks or culture, foods or cafes),
            _combo_from_groups(culture or parks, playgrounds, foods or cafes),
            _combo_from_groups(parks or culture, cafes or foods, activities),
        ]
    if style == "美食":
        return [
            _combo_from_groups(foods, cafes, parks or culture or activities),
            _combo_from_groups(foods[1:], foods, cafes or activities),
            _combo_from_groups(cafes, foods, photo or culture or activities),
        ]
    if style == "约会":
        return [
            _combo_from_groups(cafes or foods, photo or culture, parks or high_end or foods),
            _combo_from_groups(photo or culture, cafes or foods, parks or activities),
            _combo_from_groups(high_end or foods, cafes or photo, parks or culture),
        ]
    if style == "朋友":
        return [
            _combo_from_groups(foods, cafes, activities),
            _combo_from_groups(activities, foods, cafes),
            _combo_from_groups(cafes, foods, parks or culture),
        ]
    if style == "商务":
        return [
            _combo_from_groups(high_end or foods, cafes or indoor, foods[1:] or activities),
            _combo_from_groups(foods, high_end or cafes, indoor or activities),
        ]
    if style == "拍照":
        return [
            _combo_from_groups(photo, cafes, culture or parks),
            _combo_from_groups(culture, photo, cafes or foods),
            _combo_from_groups(parks, photo, cafes),
        ]
    if style == "室内":
        return [
            _combo_from_groups(indoor, cafes or foods, indoor[1:] or culture),
            _combo_from_groups(culture or indoor, foods or cafes, indoor[1:] or activities),
        ]
    if style == "自然":
        return [
            _combo_from_groups(parks, parks[1:] or activities, cafes or foods),
            _combo_from_groups(parks, cafes, culture or activities),
        ]
    if style == "文化":
        return [
            _combo_from_groups(culture, culture[1:] or indoor, cafes or foods),
            _combo_from_groups(indoor or culture, photo or cafes, foods or parks),
        ]
    if style == "省钱":
        return [
            _combo_from_groups(free_or_low, free_or_low[1:] or parks, cafes or foods),
            _combo_from_groups(parks, culture or free_or_low, foods or cafes),
        ]
    if style == "高端":
        return [
            _combo_from_groups(high_end or foods, photo or cafes, foods[1:] or activities),
            _combo_from_groups(foods, high_end or cafes, culture or photo),
        ]
    if style == "轻松":
        return [
            _combo_from_groups(cafes or parks, parks or culture, foods or cafes),
            _combo_from_groups(parks, cafes, activities),
        ]
    return []


def _category_group(place: dict) -> str:
    if _category_is_food(place):
        return "food"
    if _is_playground(place):
        return "playground"
    category = str(place.get("category") or "")
    if category in {"文化", "商场", "景点", "娱乐"}:
        return category
    return "other"


def _can_add_to_combo(place: dict, current: list[dict], max_len: int, food_limit: int | None = None) -> bool:
    group = _category_group(place)
    counts: dict[str, int] = {}
    for item in current:
        key = _category_group(item)
        counts[key] = counts.get(key, 0) + 1
    max_playground = 1 if max_len <= 3 else 2
    if _is_playground(place) and sum(1 for item in current if _is_playground(item)) >= max_playground:
        return False
    if group == "playground" and counts.get("playground", 0) >= max_playground:
        return False
    if group == "food":
        max_food = food_limit if food_limit is not None else (3 if max_len >= 4 else 2)
        return counts.get("food", 0) < max_food
    return counts.get(group, 0) < 2


def _fill_combo(combo: list[dict], ranked: list[dict], max_len: int = 3, food_limit: int | None = None) -> list[dict]:
    filled: list[dict] = []
    used: set[str] = set()
    used_base: set[str] = set()

    for place in combo:
        if len(filled) >= max_len:
            break
        base = _place_base_name(place)
        if place.get("name") in used or base in used_base:
            continue
        if not _can_add_to_combo(place, filled, max_len, food_limit=food_limit):
            continue
        filled.append(place)
        used.add(place.get("name"))
        used_base.add(base)

    for place in ranked:
        if len(filled) >= max_len:
            break
        if place.get("name") in used:
            continue
        base = _place_base_name(place)
        if base in used_base:
            continue
        if not _can_add_to_combo(place, filled, max_len, food_limit=food_limit):
            continue
        filled.append(place)
        used.add(place.get("name"))
        used_base.add(base)

    for place in ranked:
        if len(filled) >= max_len:
            break
        if place.get("name") in used:
            continue
        base = _place_base_name(place)
        if base in used_base:
            continue
        max_playground = 1 if max_len <= 3 else 2
        if _category_group(place) == "playground" and sum(1 for p in filled if _category_group(p) == "playground") >= max_playground:
            continue
        if not _can_add_to_combo(place, filled, max_len, food_limit=food_limit):
            continue
        filled.append(place)
        used.add(place.get("name"))
        used_base.add(base)
    return filled


def _combo_distance_penalty(combo: list[dict]) -> int:
    points = [
        (_float(place.get("lat")), _float(place.get("lng")))
        for place in combo
        if _float(place.get("lat")) and _float(place.get("lng"))
    ]
    if len(points) < 2:
        return 0
    max_km = 0.0
    total_km = 0.0
    for idx in range(len(points) - 1):
        km = _haversine_km(points[idx][0], points[idx][1], points[idx + 1][0], points[idx + 1][1])
        total_km += km
        max_km = max(max_km, km)
    penalty = 0
    if max_km > 12:
        penalty -= 25
    elif max_km > 8:
        penalty -= 16
    elif max_km > 5:
        penalty -= 8
    if total_km > 24:
        penalty -= 16
    elif total_km > 16:
        penalty -= 8
    return penalty


def _combo_diversity_penalty(combo: list[dict]) -> int:
    counts: dict[str, int] = {}
    for place in combo:
        group = _category_group(place)
        counts[group] = counts.get(group, 0) + 1
    penalty = 0
    if counts.get("playground", 0) > 1:
        penalty -= 35 * (counts["playground"] - 1)
    if counts.get("food", 0) >= len(combo):
        penalty -= 24
    for group, count in counts.items():
        if group not in {"food", "other"} and count > 2:
            penalty -= 12 * (count - 2)
    return penalty


def score_plans(
    ranked_places: list | None = None,
    profile: dict | None = None,
    time_plan: dict | None = None,
    current_time: dict | None = None,
    limit: int = 3,
    max_steps: int | None = None,
    goal: dict | None = None,
    request_text: str | None = None,
) -> dict:
    ranked = normalize_places(ranked_places)
    profile = profile or {}
    time_plan = time_plan or {}
    if not ranked:
        return {"candidate_plans": [], "source": "score_plans"}
    people = _people_count(profile)
    available_ranked = [place for place in ranked if not _place_reservation_blocked(place, people)]
    blocked_count = len(ranked) - len(available_ranked)
    if available_ranked:
        ranked = available_ranked

    slots = time_plan.get("slots") if isinstance(time_plan.get("slots"), list) else []
    text = str(request_text or profile.get("raw_text") or profile.get("original_text") or "")
    goal = goal or decompose_goal(text, current_user={"city": profile.get("city")})
    max_len = max_steps or _desired_step_count(text, goal, time_plan, profile)
    max_len = max(1, min(int(max_len or 3), 5))
    food_limit = _max_food_steps(text, goal, time_plan, max_len)
    activities = [p for p in ranked if not _category_is_food(p)]
    foods = [p for p in ranked if _category_is_food(p)]
    food_required = _requires_food_step(text, goal)
    if not food_required and activities:
        ranked = activities
        foods = []

    def finish_combo(combo: list[dict]) -> list[dict]:
        filled = _fill_combo(combo, ranked, max_len=max_len, food_limit=food_limit)
        if not food_required or any(_category_is_food(place) for place in filled) or not foods:
            return filled
        used = {place.get("name") for place in filled}
        food = next((place for place in foods if place.get("name") not in used), foods[0])
        if len(filled) < max_len and _can_add_to_combo(food, filled, max_len, food_limit=food_limit):
            return [*filled, food]
        if not filled:
            return [food]
        replace_at = next((idx for idx in range(len(filled) - 1, -1, -1) if not _is_playground(filled[idx])), len(filled) - 1)
        updated = list(filled)
        updated[replace_at] = food
        unique: list[dict] = []
        seen: set[str] = set()
        for place in updated:
            if place.get("name") in seen:
                continue
            unique.append(place)
            seen.add(place.get("name"))
        return unique[:max_len]

    def align_combo_to_slots(combo: list[dict]) -> list[dict]:
        if not slots or not combo:
            return combo
        ordered: list[dict] = []
        used: set[str] = set()

        def take(candidates: list[dict]) -> dict | None:
            for candidate in candidates:
                name = candidate.get("name")
                if name and name not in used:
                    used.add(name)
                    return candidate
            return None

        for slot in slots[:max_len]:
            label = str(slot.get("label") or "") if isinstance(slot, dict) else ""
            if "用餐" in label:
                picked = take([p for p in combo if _category_is_food(p)]) or take(foods)
            else:
                picked = take([p for p in combo if not _category_is_food(p)]) or take(activities) or take(combo)
            if picked:
                ordered.append(picked)
        for place in combo:
            if len(ordered) >= max_len:
                break
            name = place.get("name")
            if name and name not in used:
                ordered.append(place)
                used.add(name)
        if food_required and foods and ordered and not any(_category_is_food(place) for place in ordered):
            used_names = {place.get("name") for place in ordered}
            food = next((place for place in foods if place.get("name") not in used_names), foods[0])
            replace_at = len(ordered) - 1
            ordered[replace_at] = food
        return ordered[:max_len]

    combos: list[list[dict]] = [
        align_combo_to_slots(finish_combo(combo))
        for combo in _style_plan_combos(ranked, profile)
        if combo
    ]

    if len(ranked) >= max_len:
        combos.append(align_combo_to_slots(finish_combo(ranked[:max_len])))
    if foods and activities:
        candidate = [activities[0], foods[0]]
        if len(ranked) > 2 and ranked[2]["name"] not in {activities[0]["name"], foods[0]["name"]}:
            candidate.append(ranked[2])
        combos.append(align_combo_to_slots(finish_combo(candidate)))
    if len(ranked) >= max_len + 1:
        combos.append(align_combo_to_slots(finish_combo(ranked[1:1 + max_len])))
    for start in range(2, min(8, max(2, len(ranked) - max_len + 1))):
        combos.append(align_combo_to_slots(finish_combo(ranked[start:start + max_len])))
    if len(ranked) >= 5:
        combos.append(align_combo_to_slots(finish_combo([ranked[0], ranked[2], ranked[4]])))
        combos.append(align_combo_to_slots(finish_combo([ranked[1], ranked[3], ranked[0]])))
    if not combos:
        combos.append(ranked[: len(ranked)])

    unique: list[list[dict]] = []
    seen: set[str] = set()
    for combo in combos:
        names = tuple(place["name"] for place in combo)
        if names in seen:
            continue
        seen.add(names)
        unique.append(combo)
    scored_unique = []
    for combo in unique:
        score, breakdown, risk_penalty = _plan_score_from_combo(combo)
        adjusted = score + _combo_distance_penalty(combo) + _combo_diversity_penalty(combo)
        scored_unique.append((adjusted, score, breakdown, risk_penalty, combo))
    scored_unique.sort(key=lambda item: item[0], reverse=True)
    scored_unique = scored_unique[: max(1, min(limit, len(scored_unique)))]

    candidate_plans: list[dict] = []
    for index, (_, score, breakdown, risk_penalty, combo) in enumerate(scored_unique):
        feasible = True
        issues: list[str] = []
        for place in combo:
            blocked_reason = _place_reservation_blocked(place, people)
            if blocked_reason:
                feasible = False
                issues.append(f"{place.get('name')} {blocked_reason}，预约风险较高")
        if not feasible:
            score = max(0, score - 12)
        candidate_plans.append({
            "plan_id": f"plan_{index + 1}",
            "name": "优选方案" if index == 0 else f"备选方案{index}",
            "score": score,
            "risk_penalty": risk_penalty,
            "score_breakdown": breakdown,
            "steps": _normalize_candidate_steps(combo, profile, time_plan),
            "feasibility": feasible,
            "reservation_issues": issues,
        })

    return {"candidate_plans": candidate_plans, "source": "score_plans", "blocked_count": blocked_count}


def _stable_choice_index(seed_text: str, size: int) -> int:
    if size <= 1:
        return 0
    digest = hashlib.sha1(seed_text.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:8], 16) % size


def _place_base_name(place: dict) -> str:
    name = str(place.get("name") or "")
    name = re.sub(r"[（(].*?[）)]", "", name)
    name = re.sub(r"(北京|上海|广州|深圳|杭州|成都)?[\w\u4e00-\u9fa5]*店$", "", name)
    return name[:8] or str(place.get("name") or "")


def _is_playground(place: dict) -> bool:
    """地点是否属于乐园/游乐场类（通常需要半天以上，不适合短时间或无孩子场景）。"""
    name = str(place.get("name") or "")
    keyword = str(place.get("keyword") or "")
    category = str(place.get("category") or "")
    return any(w in name or w in keyword for w in ["乐园", "游乐", "游乐场", "儿童乐园", "亲子乐园", "亲子营地", "儿童", "亲子"])


def rank_places_for_plan(
    profile: dict,
    places: list | None = None,
    limit: int = 8,
    current_time: dict | None = None,
    time_plan: dict | None = None,
    recently_used_names: set | None = None,
) -> dict:
    """Rank search_places results. The input places should already be candidates."""
    pool = normalize_places(places)
    limit = max(1, min(int(limit or 8), 20))
    recently_used = set(recently_used_names or [])

    # 从原始请求文本判断是否明确有孩子（用于乐园惩罚）
    raw_text = str(profile.get("raw_text") or profile.get("original_text") or "")
    interest_words = _explicit_interest_words(raw_text)
    _children_words = ["亲子", "孩子", "儿童", "小孩", "宝宝", "带娃", "娃"]
    text_has_children = any(w in raw_text for w in _children_words)
    # 老人/约会场景额外强化乐园惩罚
    text_has_elder = any(w in raw_text for w in ["老人", "父母", "爸妈", "腿脚", "长辈", "爷爷", "奶奶"])
    text_is_date = any(w in raw_text for w in ["约会", "女朋友", "男朋友"])

    ranked: list[dict] = []
    aggregated_risks: list[str] = []
    for place in pool:
        rating = _float(place.get("score"))
        breakdown, risks = _place_score_breakdown(place, profile, current_time=current_time, time_plan=time_plan, pool=pool)
        interest_score = _interest_match_score(place, interest_words)
        final_score = (
            breakdown.get("profile_match", 0)
            + breakdown.get("time_fit", 0)
            + breakdown.get("distance_fit", 0)
            + breakdown.get("diet_fit", 0)
            + breakdown.get("weather_fit", 0)
            + breakdown.get("style_fit", 0)
            + breakdown.get("group_fit", 0)
            + breakdown.get("execution_fit", 0)
            + breakdown.get("price_fit", 0)
            + breakdown.get("risk_penalty", 0)
            + interest_score
            + 35
        )
        final_score = max(0, min(100, int(final_score)))

        # 最近推荐过的地点降权，避免每次推同一批
        recently_used_penalty = 0
        if recently_used and place.get("name") in recently_used:
            recently_used_penalty = -35

        # 场景过滤惩罚：无孩子时对乐园/亲子类强降权，防止历史记忆污染
        scene_penalty = 0
        if _is_playground(place):
            if text_has_elder or text_is_date:
                scene_penalty = -50  # 约会/老人场景，乐园完全不合适
            elif not text_has_children:
                scene_penalty = -40  # 没提孩子，乐园大概率不对
        elif any(w in str(place.get("keyword") or "") for w in ["亲子", "儿童"]):
            if text_has_elder or text_is_date:
                scene_penalty = -30
            elif not text_has_children:
                scene_penalty = -20

        # 小随机扰动（±8），打破同分平局，让每次请求结果有差异
        jitter = random.randint(-8, 8)

        display_score = final_score  # reason 里展示原始分
        sort_score = final_score + recently_used_penalty + scene_penalty + jitter

        reasons: list[str] = []
        if rating:
            reasons.append(f"评分{rating:.1f}")
        if breakdown["profile_match"] >= 10:
            reasons.append("与用户画像高度匹配")
        if breakdown["time_fit"] >= 15:
            reasons.append("营业时间与预计时间高度匹配")
        elif breakdown["time_fit"] >= 8:
            reasons.append("营业时间与预计时间较为匹配")
        if breakdown["diet_fit"] >= 12:
            reasons.append("饮食偏好命中")
        if breakdown["group_fit"] >= 12:
            reasons.append("同行人需求匹配")
        if breakdown["distance_fit"] >= 12:
            if profile.get("user_lat") and profile.get("user_lng"):
                reasons.append("距离你当前位置较近")
            else:
                reasons.append("距离候选地点较近")
        if breakdown["execution_fit"] >= 10:
            reasons.append("执行可行性高")
        if breakdown.get("price_fit", 6) >= 12:
            reasons.append("价格符合预算偏好")
        elif breakdown.get("price_fit", 6) <= 3:
            reasons.append("价格偏高于预算")
        if breakdown.get("style_fit", 0) >= 18:
            reasons.append(f"符合{profile.get('route_style') or '当前'}风格")
        if interest_score >= 18:
            reasons.append("命中明确兴趣点")
        if not reasons:
            reasons.append("基础信息匹配")

        if risks:
            aggregated_risks.extend(risks)

        ranked.append({
            **place,
            "score": display_score,
            "final_score": display_score,
            "_sort_score": sort_score,
            "score_breakdown": breakdown,
            "risks": risks,
            "reason": "，".join(reasons[:4]),
        })

    ranked.sort(key=lambda p: p["_sort_score"], reverse=True)
    # 清除内部排序字段，不暴露给前端
    for p in ranked:
        p.pop("_sort_score", None)
    return {
        "ranked_places": ranked[:limit],
        "source": "search_results",
        "risks": list(dict.fromkeys(aggregated_risks)),
    }


def _parse_price_amount(price_range: str) -> float | None:
    """Extract average numeric price from a price_range string like '人均¥88-128'."""
    text = (price_range or "").strip()
    if not text:
        return None
    if any(w in text for w in ["免费", "Free", "free"]):
        return 0.0
    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _score_place_price_fit(place: dict, profile: dict) -> int:
    budget = profile.get("budget") or []
    if not budget:
        return 6
    amount = _parse_price_amount(str(place.get("price_range") or ""))
    if amount is None:
        return 5
    if "偏经济" in budget:
        if amount == 0:
            return 16
        if amount <= 50:
            return 16
        if amount <= 100:
            return 12
        if amount <= 200:
            return 6
        return 2
    if "偏品质" in budget:
        if amount >= 200:
            return 16
        if amount >= 100:
            return 12
        if amount >= 50:
            return 8
        return 4
    return 6


def _calculate_total_price(places: list[dict]) -> str:
    total = 0.0
    has_any = False
    for place in places:
        amount = _parse_price_amount(str(place.get("price_range") or ""))
        if amount is not None:
            total += amount
            has_any = True
    if not has_any or total <= 0:
        return ""
    return f"¥{int(total)}"


def _personalized_intro(profile: dict, steps: list) -> str:
    """根据实际 profile 生成个性化的规划说明。"""
    people = profile.get("people") or {}
    scene = profile.get("scene") or []
    constraints = profile.get("constraints") or []
    preferences = profile.get("preferences") or []
    time_pref = profile.get("time_preference") or ""
    adults = int(people.get("adults") or 0)
    children = int(people.get("children") or 0)
    child_age = people.get("child_age") or ""

    who_parts = []
    if adults >= 2:
        who_parts.append(f"{adults}大人")
    if children > 0:
        who_parts.append(f"{children}孩子{'（' + child_age + '）' if child_age else ''}")
    who = "你" + ("+" + "+".join(who_parts) if who_parts else "")

    key_tags = []
    if any("距离" in c for c in constraints):
        key_tags.append("不跑太远")
    if any(k in " ".join(preferences + constraints) for k in ["低负担", "清淡", "减肥", "少油"]):
        key_tags.append("饮食低负担")
    if "亲子出行" in scene or children > 0:
        key_tags.append("亲子友好")

    time_str = time_pref if (time_pref and time_pref != "未明确") else "今天"
    tag_str = f"，兼顾{'、'.join(key_tags)}" if key_tags else ""
    place_names = [s.get("name") for s in steps if isinstance(s, dict) and s.get("name")]
    route = " → ".join(place_names[:4]) if place_names else ""

    intro = f"已为{who}{time_str}出行安排好路线{tag_str}"
    if route:
        intro += f"：{route}"
    return intro + "。"


def _personalized_thinking(profile: dict, steps: list, weather_result: dict | None) -> list:
    """根据 profile、steps reason 和天气生成用户可读的决策过程。"""
    people = profile.get("people") or {}
    scene = profile.get("scene") or []
    constraints = profile.get("constraints") or []
    preferences = profile.get("preferences") or []
    time_pref = profile.get("time_preference") or ""
    adults = int(people.get("adults") or 0)
    children = int(people.get("children") or 0)
    child_age = people.get("child_age") or ""

    thinking: list[str] = []

    # 同行人 & 时间
    people_desc = people.get("description") or ""
    if adults or children:
        if not people_desc:
            people_desc = (f"{adults}大人" if adults else "") + (f"+{children}孩子" if children else "")
        time_note = f"，{time_pref}" if (time_pref and time_pref != "未明确") else ""
        thinking.append(f"同行人：{people_desc}{time_note}")

    # 距离约束
    if any("距离" in c for c in constraints):
        thinking.append("优先距离较近的地点，动线不累")

    # 饮食约束
    diet_flags = [k for k in preferences + constraints
                  if any(w in k for w in ["低负担", "清淡", "减肥", "少油", "忌口"])]
    if diet_flags:
        thinking.append(f"饮食考虑：{diet_flags[0]}")

    # 孩子
    if children > 0:
        age_str = f"（{child_age}）" if child_age else ""
        thinking.append(f"有{children}名孩子{age_str}同行，优先安全低强度场所")

    # 老人/腿脚
    for c in constraints:
        if any(k in c for k in ["老人", "腿脚", "父母"]):
            thinking.append(c)
            break

    # 天气
    if isinstance(weather_result, dict) and weather_result.get("status") == "ok":
        msg = weather_result.get("message") or ""
        risks = weather_result.get("outdoor_risk") or []
        if msg:
            risk_note = f"，提醒：{'、'.join(risks)}" if risks else ""
            thinking.append(f"实时天气：{msg}{risk_note}")
    elif isinstance(weather_result, dict) and weather_result.get("status") == "unavailable":
        msg = weather_result.get("message") or "实时天气暂不可用"
        thinking.append(f"实时天气：{msg}，已按室内/低天气风险方案保守处理")

    # 各地点选择理由
    for step in steps[:4]:
        if not isinstance(step, dict) or not step.get("name"):
            continue
        reason = (step.get("reason") or "").strip()
        name = step.get("name")
        if reason:
            thinking.append(f"选「{name}」：{reason[:60]}")

    return [t for t in thinking if t][:6] or ["已综合考虑时间、偏好和地点评分为你生成路线"]


def _selection_reason_text(
    profile: dict,
    selected_plan: dict | None,
    candidate_plans: list[dict],
    request_text: str,
    blocked_count: int = 0,
) -> str:
    if not selected_plan:
        return "当前真实地点候选不足，我保留了可执行信息，避免编造路线。"
    steps = selected_plan.get("steps") if isinstance(selected_plan.get("steps"), list) else []
    names = [str(step.get("name") or "") for step in steps if isinstance(step, dict) and step.get("name")]
    style = str(profile.get("route_style") or "")
    time_pref = str(profile.get("time_preference") or "")
    parts: list[str] = []
    if blocked_count:
        parts.append(f"已避开{blocked_count}个座位不足或状态异常的地点")
    if time_pref and time_pref != "未明确":
        parts.append(f"按{time_pref}安排")
    if style and style != "均衡":
        parts.append(f"贴合{style}场景")
    if any(word in request_text for word in ["看展", "展览", "博物馆", "美术馆"]):
        parts.append("优先保留看展/文化体验")
    if any(word in request_text for word in ["吃", "餐", "饭", "预约", "预订"]):
        parts.append("用餐点优先选择当前可预约概率更高的候选")
    if len(names) >= 2:
        parts.append(f"动线围绕「{names[0]}」到「{names[-1]}」收束")
    elif names:
        parts.append(f"最终选择「{names[0]}」作为最稳妥地点")
    if not parts:
        parts.append("综合匹配度、距离、时间窗口和执行风险后选出当前方案")
    suffix = f"，共比较了{len(candidate_plans)}个候选方案。" if candidate_plans else "。"
    return "，".join(parts) + suffix


def mock_reserve(place_name: str, time: str = "17:30", people_count: int | None = None) -> dict:
    people_part = f"，人数{people_count}人" if people_count else ""
    return {
        "type": "reserve",
        "target": place_name or "待定地点",
        "time": time,
        "people_count": people_count or 1,
        "status": "pending",
        "message": f"是否预约{place_name or '待定地点'}？时间{time}{people_part}",
    }


def mock_create_order(target: str = "智能行程组合预订", price: str = "") -> dict:
    return {
        "type": "create_order",
        "target": target,
        "status": "mock_success",
        "order_id": f"mock_{int(time.time() * 1000)}",
        "price": price,
        "message": f"已生成模拟订单{f'，预估总价{price}' if price else ''}",
    }


def _action_targets_from_text(text: str, profile: dict) -> dict:
    people = profile.get("people") if isinstance(profile.get("people"), dict) else {}
    return {
        "has_partner": any(word in text for word in ["老婆", "妻子", "爱人", "女朋友", "对象", "伴侣", "老公", "丈夫", "男朋友", "约会"])
        or "伴侣同行" in (profile.get("scene") or []),
        "has_friends": any(word in text for word in ["朋友", "同学", "同事", "聚会"])
        or "朋友聚会" in (profile.get("scene") or [])
        or "朋友" in str(people.get("description") or ""),
        "has_children": int(people.get("children") or 0) > 0
        or any(word in text for word in ["孩子", "小孩", "宝宝", "儿子", "女儿", "亲子", "带娃"]),
    }


def _build_role_message(target: str, steps: list[dict], profile: dict, request_text: str) -> str:
    route = " -> ".join(
        f"{step.get('time', '')} {step.get('name', '')}".strip()
        for step in steps[:5]
        if isinstance(step, dict) and step.get("name")
    )
    if target in {"老婆", "伴侣"}:
        diet_note = "，晚餐已尽量选低负担/清淡可选" if any(word in request_text for word in ["减肥", "清淡", "低脂", "低卡", "少油"]) else ""
        return f"我把下午安排好了：{route}{diet_note}。你看看这个节奏可以吗？"
    if target == "朋友":
        return f"搞定了，下午按这个来：{route}。我这边先把预约草稿准备好，大家确认后出发。"
    return f"方案已安排好：{route}。确认后我来执行预约和下单。"


def _extract_cn_phones(text: str) -> list[str]:
    phones: list[str] = []
    for match in re.finditer(r"(?:\+?86[\s-]?)?1[3-9]\d(?:[\s-]?\d{4}){2}", str(text or "")):
        cleaned = re.sub(r"[\s-]", "", match.group(0))
        if cleaned.startswith("86") and not cleaned.startswith("+86"):
            cleaned = "+" + cleaned
        elif not cleaned.startswith("+86"):
            cleaned = "+86" + cleaned
        if cleaned not in phones:
            phones.append(cleaned)
    return phones[:3]


def _build_action_bundle(
    steps: list[dict],
    profile: dict,
    request_text: str,
    time_plan: dict,
    actions: list[dict],
    selected_plan: dict | None,
    score_summary: dict,
) -> dict:
    """Create a user-confirmable execution bundle for the final plan."""
    if not steps:
        return {
            "status": "unavailable",
            "summary": "当前没有可执行路线，暂不生成动作清单。",
            "items": [],
        }

    people = _people_count(profile)
    target_flags = _action_targets_from_text(request_text, profile)
    step_place_names = {step.get("name") for step in steps if isinstance(step, dict)}
    existing_reserve_targets = {
        action.get("target")
        for action in actions
        if isinstance(action, dict) and action.get("type") == "reserve" and action.get("target")
    }

    items: list[dict] = []

    reserve_steps = [
        step for step in steps
        if isinstance(step, dict)
        and step.get("name")
        and (
            _category_is_food(step)
            or step.get("name") in existing_reserve_targets
            or any(word in request_text for word in ["预约", "预订", "订座", "订位", "订票", "下单"])
        )
    ]
    if not reserve_steps:
        reserve_steps = [steps[-1]]
    for step in reserve_steps[:2]:
        items.append({
            "id": f"reserve_{len(items) + 1}",
            "type": "reserve",
            "target": step.get("name") or "",
            "title": f"预约 {step.get('name') or '地点'}",
            "status": "ready",
            "time": step.get("time") or time_plan.get("start") or "待定",
            "date": step.get("date") or time_plan.get("date") or "",
            "people_count": people,
            "price": step.get("price_range") or "",
            "payload": {
                "place_name": step.get("name") or "",
                "time": step.get("time") or "",
                "date": step.get("date") or time_plan.get("date") or "",
                "people_count": people,
                "price": step.get("price_range") or "",
            },
            "message": "已检查候选地点余位/状态，并生成预约草稿。",
        })

    explicit_gift_words = ["鲜花", "花束", "买花", "送花", "蛋糕", "甜品", "惊喜", "纪念日", "纪念"]
    if target_flags["has_partner"] and any(word in request_text for word in explicit_gift_words):
        if any(word in request_text for word in ["鲜花", "花束", "买花", "送花"]):
            gift_target = "鲜花"
        elif "蛋糕" in request_text:
            gift_target = "蛋糕"
        elif "甜品" in request_text:
            gift_target = "甜品"
        else:
            gift_target = "小惊喜"
        deliver_to = reserve_steps[0].get("name") if reserve_steps else steps[-1].get("name")
        gift_reason = "用户明确提出礼品/惊喜需求，准备到店或配送安排。"
        items.append({
            "id": f"order_gift_{len(items) + 1}",
            "type": "order_gift",
            "target": gift_target,
            "title": f"下单 {gift_target}",
            "status": "ready",
            "time": reserve_steps[0].get("time") if reserve_steps else time_plan.get("start", ""),
            "payload": {"target": gift_target, "deliver_to": deliver_to, "reason": gift_reason},
            "message": f"已准备{gift_target}下单草稿，送到「{deliver_to}」。原因：{gift_reason}",
        })

    if target_flags["has_friends"]:
        items.append({
            "id": f"notify_friend_{len(items) + 1}",
            "type": "notify",
            "target": "朋友",
            "title": "通知朋友",
            "status": "ready",
            "message": _build_role_message("朋友", steps, profile, request_text),
        })

    if target_flags["has_partner"]:
        items.append({
            "id": f"share_partner_{len(items) + 1}",
            "type": "share",
            "target": "老婆" if "老婆" in request_text else "伴侣",
            "title": "发给伴侣确认",
            "status": "ready",
            "message": _build_role_message("老婆", steps, profile, request_text),
        })

    if target_flags["has_children"] and not target_flags["has_partner"]:
        items.append({
            "id": f"share_family_{len(items) + 1}",
            "type": "share",
            "target": "家人",
            "title": "发给家人确认",
            "status": "ready",
            "message": _build_role_message("家人", steps, profile, request_text),
        })

    for phone in _extract_cn_phones(request_text):
        items.append({
            "id": f"sms_{len(items) + 1}",
            "type": "sms",
            "target": phone[:5] + "****" + phone[-4:],
            "title": "发送短信通知",
            "status": "ready",
            "message": _build_role_message("联系人", steps, profile, request_text),
            "payload": {
                "phone": phone,
                "message": _build_role_message("联系人", steps, profile, request_text),
            },
        })

    # 分享行程（始终提供，客户端生成二维码）
    route_preview = " → ".join(s.get("name", "") for s in steps[:3] if isinstance(s, dict) and s.get("name"))
    items.append({
        "id": f"share_route_{len(items) + 1}",
        "type": "share_route",
        "target": "行程二维码",
        "title": "分享行程",
        "status": "ready",
        "message": f"生成当前路线二维码：{route_preview}，对方扫码即可在浏览器中查看完整行程方案。",
    })

    # 加入日历提醒（有具体时间时提供）
    start_time = time_plan.get("start") or (steps[0].get("time") if steps else "")
    date_str = time_plan.get("date") or (steps[0].get("date") if steps else "")
    if start_time:
        cal_title = f"半日出行：{route_preview[:20]}" if route_preview else "半日出行提醒"
        items.append({
            "id": f"save_calendar_{len(items) + 1}",
            "type": "save_calendar",
            "target": "日历",
            "title": "加入日历提醒",
            "status": "ready",
            "message": f"将行程「{cal_title}」添加到手机日历，{date_str or '今天'} {start_time} 出发前提醒。",
            "payload": {
                "title": cal_title,
                "start_time": start_time,
                "date": date_str,
                "steps": [{"name": s.get("name", ""), "time": s.get("time", "")} for s in steps[:4] if isinstance(s, dict)],
            },
        })

    return {
        "status": "awaiting_confirmation",
        "summary": "已生成可执行清单：预约草稿、同行人通知、行程分享和日历提醒均已准备好，勾选后一次执行。",
        "items": items,
        "selected_plan_id": selected_plan.get("plan_id") if isinstance(selected_plan, dict) else "",
        "score_summary": score_summary,
        "confirm_required": True,
    }


def _people_count(profile: dict) -> int:
    people = profile.get("people") or {}
    return int(people.get("adults") or 0) + int(people.get("children") or 0) or 1


def _start_time(profile: dict) -> str:
    value = profile.get("time_preference") or ""
    match = re.search(r"([0-9]{1,2})点", value)
    if match:
        hour = int(match.group(1))
        if "下午" in value and hour < 12:
            hour += 12
        return f"{hour:02d}:00"
    if "下午" in value:
        return "14:00"
    if "晚上" in value:
        return "18:00"
    if "上午" in value:
        return "10:00"
    return "14:00"


def _next_time(time_text: str, hours: int) -> str:
    hour, minute = [int(x) for x in time_text.split(":")]
    hour += hours
    return f"{hour:02d}:{minute:02d}"


def _category_is_food(place: dict) -> bool:
    text = _as_text(place)
    return place.get("category") in {"餐厅", "咖啡", "酒吧"} or _contains_any(
        text,
        ["餐饮服务", "餐饮相关", "餐厅", "快餐厅", "咖啡", "茶艺馆", "茶馆", "海鲜", "烤鸭", "火锅", "小吃", "美食", "酒吧"],
    )


def _goal_task_slots(goal: dict | None) -> list[str]:
    if not isinstance(goal, dict):
        return []
    slots = goal.get("task_slots")
    return [str(item) for item in slots if item] if isinstance(slots, list) else []


def _total_plan_minutes(time_plan: dict | None) -> int:
    if not isinstance(time_plan, dict):
        return 240
    start = _minute_from_clock(str(time_plan.get("start") or ""))
    end = _minute_from_clock(str(time_plan.get("end") or ""))
    if start is None or end is None:
        slots = time_plan.get("slots") if isinstance(time_plan.get("slots"), list) else []
        if slots:
            first = _minute_from_clock(str(slots[0].get("start") or ""))
            last = _minute_from_clock(str(slots[-1].get("end") or ""))
            if first is not None and last is not None and last > first:
                return last - first
        return 240
    if end <= start:
        end += 24 * 60
    return max(30, end - start)


def _single_place_intent(text: str, goal: dict | None = None) -> bool:
    text = str(text or "")
    if _no_meal_request(text):
        return False
    task_slots = _goal_task_slots(goal)
    route_words = ["路线", "行程", "半日", "一天", "一日", "游", "玩", "逛", "散步", "看展", "先", "再", "加", "多个", "几个", "安排一上午", "安排一下午"]
    one_place_words = ["推荐一家", "推荐一个", "找一家", "找一个", "找个", "去哪吃", "吃什么", "订个", "约个"]
    meal_words = ["晚饭", "晚餐", "午饭", "午餐", "早餐", "夜宵", "吃饭", "餐厅", "饭馆", "火锅", "烤鸭", "宴请"]
    drink_words = ["咖啡", "喝茶", "茶馆", "甜品"]
    explicit_one = any(word in text for word in one_place_words) or any(word in text for word in ["就一个", "只要一个", "一家就行"])
    has_route = any(word in text for word in route_words)
    only_meal_task = "用餐" in task_slots and "活动" not in task_slots
    if explicit_one:
        return True
    if "活动" in task_slots:
        return False
    if only_meal_task and any(word in text for word in meal_words + drink_words):
        return True
    if any(word in text for word in meal_words) and not has_route:
        return True
    return False


def _desired_step_count(text: str, goal: dict | None, time_plan: dict | None, profile: dict | None = None) -> int:
    if _single_place_intent(text, goal):
        return 1
    total_minutes = _total_plan_minutes(time_plan)
    task_slots = _goal_task_slots(goal)
    relaxed = any(word in str(text or "") for word in ["别太累", "不要太累", "不太累", "轻松一点", "别折腾"])
    if "用餐" in task_slots and "活动" not in task_slots:
        return 1
    if relaxed and "用餐" in task_slots and "活动" in task_slots and total_minutes <= 240:
        return 2
    if "用餐" in task_slots and "活动" in task_slots and 180 < total_minutes <= 360:
        return 3
    if total_minutes <= 90:
        return 1
    if total_minutes <= 180:
        return 2
    if total_minutes <= 240:
        return 3
    if total_minutes <= 360:
        return 4
    return 5


def _max_food_steps(text: str, goal: dict | None, time_plan: dict | None, max_steps: int) -> int:
    if _no_meal_request(text):
        return 0
    if max_steps <= 1 or _single_place_intent(text, goal):
        return 1
    task_slots = _goal_task_slots(goal)
    total_minutes = _total_plan_minutes(time_plan)
    style_food = any(word in text for word in ["美食路线", "小吃路线", "吃吃逛逛", "探店", "美食半日"])
    if "用餐" in task_slots and "活动" not in task_slots:
        return 1
    if total_minutes <= 210:
        return 1
    if style_food and total_minutes >= 240:
        return min(2, max_steps)
    return 1 if max_steps <= 3 else 2


def _requires_food_step(text: str, goal: dict | None) -> bool:
    if _no_meal_request(text):
        return False
    if "用餐" in _goal_task_slots(goal):
        return True
    return any(word in str(text or "") for word in ["吃", "晚饭", "晚餐", "午饭", "午餐", "早餐", "夜宵", "餐厅", "饭馆", "火锅", "烤鸭", "美食", "聚餐", "宴请"])


def build_final_plan(
    profile: dict,
    ranked_places: list | None = None,
    actions: list | None = None,
    observations: list | None = None,
    current_time: dict | None = None,
    goal: dict | None = None,
    time_plan: dict | None = None,
    risks: list | None = None,
    original_text: str | None = None,
) -> dict:
    """Build final JSON from ranked real places only."""
    ranked_places = normalize_places(ranked_places)
    actions = actions or []
    observations = observations or []
    current_time = current_time or get_current_time()
    request_text = str(original_text or profile.get("raw_text") or "")
    goal = goal or decompose_goal(request_text, current_user={"city": profile.get("city")})
    time_plan = time_plan or plan_time_slots(
        text=str(request_text or goal.get("raw_text") or ""),
        current_time=current_time,
        goal=goal,
    )
    risks = list(risks or [])
    for warning in time_plan.get("warnings") or []:
        if warning and warning not in risks:
            risks.append(str(warning))

    weather_result = None
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        if observation.get("tool_name") == "get_weather" or observation.get("skill_name") == "get_weather":
            result = observation.get("result")
            if isinstance(result, dict):
                weather_result = result
                break

    chosen: list[dict] = []

    def add_place(place: dict | None) -> None:
        if place and all(existing.get("name") != place.get("name") for existing in chosen):
            chosen.append(place)

    activity_places = [p for p in ranked_places if not _category_is_food(p)]
    food_places = [p for p in ranked_places if _category_is_food(p)]
    restaurant_places = [p for p in food_places if p.get("category") == "餐厅"]

    add_place(next((p for p in activity_places if p.get("category") == "景点"), None))
    if len(chosen) < 2:
        add_place(next((p for p in activity_places if p.get("name") not in {c.get("name") for c in chosen}), None))
    add_place(restaurant_places[0] if restaurant_places else (food_places[0] if food_places else None))
    for place in ranked_places:
        if len(chosen) >= 4:
            break
        add_place(place)
    chosen = chosen[:4]

    steps = []
    slots = time_plan.get("slots") if isinstance(time_plan.get("slots"), list) else []
    for index, place in enumerate(chosen):
        slot = slots[index] if index < len(slots) and isinstance(slots[index], dict) else None
        current = str(slot.get("start")) if slot else _next_time(time_plan.get("start") or _start_time(profile), index)
        step_date = slot.get("date") if slot else (time_plan.get("date") or "")
        duration = "约2小时" if index == 0 else ("约1小时" if place.get("category") in ["公园", "咖啡"] else "约1.5小时")
        _desc = (place.get("desc_text") or "").strip()
        _price = (place.get("price_range") or "").strip()
        _parts = [p for p in [_desc, _price] if p]
        meta = "；".join(_parts) or (place.get("reason") or "匹配当前需求")
        steps.append({
            "time": current,
            "date": step_date,
            "name": place["name"],
            "meta": meta,
            "reason": str(place.get("reason") or ""),
            "category": place.get("category", ""),
            "lng": place.get("lng", 0.0),
            "lat": place.get("lat", 0.0),
            "source": place.get("source", ""),
            "source_id": place.get("source_id", ""),
            "phone": place.get("phone", ""),
            "address": place.get("address", ""),
            "open_hours": place.get("open_hours", ""),
            "price_range": place.get("price_range", ""),
            "desc_text": place.get("desc_text", ""),
            "score": place.get("score", ""),
            "available_seats": place.get("available_seats"),
            "capacity_status": place.get("capacity_status", ""),
        })

    total_price = _calculate_total_price(chosen)

    if not chosen:
        actions = []
        intro = "我没有拿到可用的地点库数据，暂时无法生成不凭空编造地点的路线。"
        thinking = [
            "Agent 已完成用户画像分析",
            "当前没有可用于规划的真实 places 候选",
            "为避免编造地点，最终路线保持为空，等待 main.py 传入 places 数据",
        ]
    else:
        intro = _personalized_intro(profile, steps)
        thinking = _personalized_thinking(profile, steps, weather_result)
        if weather_result and weather_result.get("status") != "ok":
            thinking.insert(0, "实时天气工具暂不可用，规划采用室内/低风险选择")
        step_names = {step["name"] for step in steps}
        actions = [
            action for action in actions
            if action.get("type") != "reserve" or action.get("target") in step_names
        ]

    desired_step_count = _desired_step_count(request_text, goal, time_plan, profile)
    food_limit = _max_food_steps(request_text, goal, time_plan, desired_step_count)
    plan_ranked_places = ranked_places
    if _single_place_intent(request_text, goal):
        meal_candidates = [place for place in ranked_places if _category_is_food(place)]
        if "用餐" in _goal_task_slots(goal) and meal_candidates:
            plan_ranked_places = meal_candidates

    candidate_data = score_plans(
        plan_ranked_places,
        profile=profile,
        time_plan=time_plan,
        current_time=current_time,
        limit=6,
        max_steps=desired_step_count,
        goal=goal,
        request_text=request_text,
    )
    candidate_plans = candidate_data.get("candidate_plans") or []
    blocked_candidate_count = int(candidate_data.get("blocked_count") or 0)
    selected_plan = None
    if candidate_plans:
        feasible_plans = [plan for plan in candidate_plans if plan.get("feasibility")] or candidate_plans
        best_score = max(int(plan.get("score") or 0) for plan in feasible_plans)
        near_best = [
            plan for plan in feasible_plans
            if int(plan.get("score") or 0) >= best_score - 8
        ]
        seed = "|".join([
            request_text,
            str(profile.get("route_style") or ""),
            str(profile.get("time_preference") or ""),
            str(current_time.get("date") or ""),
            str(current_time.get("hour") or ""),
        ])
        selected_plan = near_best[_stable_choice_index(seed, len(near_best))]
        plan_date = time_plan.get("date") or ""
        steps = [
            {
                "time": step.get("time") or "",
                "date": step.get("date") or plan_date,
                "name": step.get("name") or "",
                "meta": step.get("meta") or "",
                "reason": str(step.get("reason") or ""),
                "lng": float(step.get("lng") or 0) if isinstance(step.get("lng"), (int, float, str)) else 0.0,
                "lat": float(step.get("lat") or 0) if isinstance(step.get("lat"), (int, float, str)) else 0.0,
                "source": step.get("source") or "",
                "source_id": step.get("source_id") or "",
                "phone": step.get("phone") or "",
                "address": step.get("address") or "",
                "open_hours": step.get("open_hours") or "",
                "price_range": step.get("price_range") or "",
                "desc_text": step.get("desc_text") or "",
                "score": step.get("score") or "",
                "available_seats": step.get("available_seats"),
                "capacity_status": step.get("capacity_status") or "",
            }
            for step in selected_plan.get("steps") or []
        ]
        if not steps:
            # fallback use chosen places if candidate plan has no normalized steps
            steps = []
            for index, place in enumerate(chosen):
                slot = slots[index] if index < len(slots) and isinstance(slots[index], dict) else None
                current = str(slot.get("start")) if slot else _next_time(time_plan.get("start") or _start_time(profile), index)
                step_date = slot.get("date") if slot else plan_date
                _desc = (place.get("desc_text") or "").strip()
                _price = (place.get("price_range") or "").strip()
                _parts = [p for p in [_desc, _price] if p]
                meta = "；".join(_parts) or (place.get("reason") or "匹配当前需求")
                steps.append({
                    "time": current,
                    "date": step_date,
                    "name": place["name"],
                    "meta": meta,
                    "lng": place.get("lng", 0.0),
                    "lat": place.get("lat", 0.0),
                    "source": place.get("source", ""),
                    "source_id": place.get("source_id", ""),
                    "phone": place.get("phone", ""),
                    "address": place.get("address", ""),
                    "open_hours": place.get("open_hours", ""),
                    "price_range": place.get("price_range", ""),
                    "desc_text": place.get("desc_text", ""),
                    "score": place.get("score", ""),
                    "available_seats": place.get("available_seats"),
                    "capacity_status": place.get("capacity_status", ""),
                })

        # 根据 slots 数量补充步骤（全天/长时窗口允许4-5个地点）
        desired_count = desired_step_count
        if len(steps) < desired_count:
            used_names = {s.get("name") for s in steps}
            step_place_map_for_fill = {place.get("name"): place for place in ranked_places}
            current_places = [
                step_place_map_for_fill.get(s.get("name"), {"name": s.get("name"), "category": s.get("category")})
                for s in steps
                if s.get("name")
            ]
            used_bases = {_place_base_name(place) for place in current_places}
            for extra_idx in range(len(steps), desired_count):
                slot = slots[extra_idx] if extra_idx < len(slots) else None
                if not slot:
                    break
                extra = next(
                    (
                        p for p in ranked_places
                        if p.get("name") not in used_names
                        and _place_base_name(p) not in used_bases
                        and _can_add_to_combo(p, current_places, desired_count, food_limit=food_limit)
                    ),
                    None,
                )
                if not extra:
                    break
                e_time = slot.get("start", "")
                e_date = slot.get("date", plan_date)
                e_desc = (extra.get("desc_text") or "").strip()
                e_price = (extra.get("price_range") or "").strip()
                e_meta = "；".join(p for p in [e_desc, e_price] if p) or "匹配当前需求"
                steps.append({
                    "time": e_time,
                    "date": e_date,
                    "name": extra["name"],
                    "meta": e_meta,
                    "reason": str(extra.get("reason") or ""),
                    "lng": float(extra.get("lng") or 0),
                    "lat": float(extra.get("lat") or 0),
                    "source": extra.get("source", ""),
                    "source_id": extra.get("source_id", ""),
                    "phone": extra.get("phone", ""),
                    "address": extra.get("address", ""),
                    "open_hours": extra.get("open_hours", ""),
                    "price_range": extra.get("price_range", ""),
                    "desc_text": extra.get("desc_text", ""),
                    "score": extra.get("score", ""),
                    "available_seats": extra.get("available_seats"),
                    "capacity_status": extra.get("capacity_status", ""),
                })
                used_names.add(extra["name"])
                used_bases.add(_place_base_name(extra))
                current_places.append(extra)
        interest_words = _explicit_interest_words(request_text)
        if steps and interest_words and not any(_place_matches_interest(step, interest_words) for step in steps):
            interest_place = next((p for p in ranked_places if _place_matches_interest(p, interest_words)), None)
            if interest_place:
                first_time = steps[0].get("time") or time_plan.get("start") or ""
                first_date = steps[0].get("date") or time_plan.get("date") or ""
                desc = (interest_place.get("desc_text") or "").strip()
                price = (interest_place.get("price_range") or "").strip()
                meta = "；".join(p for p in [desc, price] if p) or (interest_place.get("reason") or "命中明确兴趣点")
                steps[0] = {
                    "time": first_time,
                    "date": first_date,
                    "name": interest_place["name"],
                    "meta": meta,
                    "reason": str(interest_place.get("reason") or "命中明确兴趣点"),
                    "lng": float(interest_place.get("lng") or 0),
                    "lat": float(interest_place.get("lat") or 0),
                    "source": interest_place.get("source", ""),
                    "source_id": interest_place.get("source_id", ""),
                    "phone": interest_place.get("phone", ""),
                    "address": interest_place.get("address", ""),
                    "open_hours": interest_place.get("open_hours", ""),
                    "price_range": interest_place.get("price_range", ""),
                    "desc_text": interest_place.get("desc_text", ""),
                    "score": interest_place.get("score", ""),
                    "available_seats": interest_place.get("available_seats"),
                    "capacity_status": interest_place.get("capacity_status", ""),
                }
        if steps:
            intro = _personalized_intro(profile, steps)
            thinking = _personalized_thinking(profile, steps, weather_result)
    feasibility = bool(selected_plan.get("feasibility")) if isinstance(selected_plan, dict) else False
    reservation_issues = []
    if any(action.get("type") == "reserve" and action.get("status") == "mock_failed" for action in actions):
        feasibility = False
        failure_messages = [action.get("message") for action in actions if action.get("type") == "reserve" and action.get("status") == "mock_failed"]
        for message in failure_messages:
            if message and message not in risks:
                risks.append(message)
            if message and message not in reservation_issues:
                reservation_issues.append(message)

    if selected_plan and not selected_plan.get("feasibility"):
        if "当前优选方案可能因预约失败或座位不足而不可行" not in risks:
            risks.append("当前优选方案可能因预约失败或座位不足而不可行。")
        if "当前方案存在预约可行性风险，请优先确认座位或选择备选方案。" not in thinking:
            thinking.insert(0, "当前方案存在预约可行性风险，请优先确认座位或选择备选方案。")
    elif selected_plan and selected_plan.get("feasibility"):
        if "已优先选择当前可预约成功概率较高的方案。" not in thinking:
            thinking.insert(0, "已优先选择当前可预约成功概率较高的方案。")

    final_step_names = {step.get("name") for step in steps if isinstance(step, dict)}
    actions = [
        action for action in actions
        if action.get("type") != "reserve" or action.get("target") in final_step_names
    ]
    wants_reserve = any(word in request_text for word in ["预约", "预订", "订座", "下单"])
    if wants_reserve and steps and not any(action.get("type") == "reserve" for action in actions):
        step_place_map = {place.get("name"): place for place in ranked_places}
        reserve_step = next(
            (
                step for step in steps
                if _category_is_food(step_place_map.get(step.get("name"), {"name": step.get("name")}))
            ),
            steps[-1],
        )
        actions.insert(
            0,
            mock_reserve(
                reserve_step.get("name", ""),
                time=reserve_step.get("time") or time_plan.get("start") or "17:30",
                people_count=_people_count(profile),
            ),
        )

    score_summary = {
        "candidate_count": len(candidate_plans),
        "selected_score": selected_plan.get("score") if isinstance(selected_plan, dict) else 0,
        "selected_risk_penalty": selected_plan.get("risk_penalty") if isinstance(selected_plan, dict) else 0,
        "selected_feasibility": feasibility,
        "selected_reservation_issues": reservation_issues,
        "selection_reason": _selection_reason_text(
            profile,
            selected_plan,
            candidate_plans,
            request_text,
            blocked_candidate_count,
        ),
    }
    action_bundle = _build_action_bundle(
        steps=steps,
        profile=profile,
        request_text=request_text,
        time_plan=time_plan,
        actions=actions,
        selected_plan=selected_plan,
        score_summary=score_summary,
    )
    if steps and action_bundle.get("items"):
        execution_note = "已生成预约/下单/通知草稿，确认后可一键执行。"
        if execution_note not in thinking:
            thinking.insert(0, execution_note)

    return {
        "type": "plan",
        "intro": intro,
        "profile": profile,
        "thinking": thinking,
        "steps": steps,
        "actions": actions,
        "current_time": current_time,
        "goal": goal,
        "time_plan": time_plan,
        "risks": risks,
        "feasibility": feasibility,
        "candidate_plans": candidate_plans,
        "selected_plan": selected_plan,
        "score_summary": score_summary,
        "action_bundle": action_bundle,
    }


def build_mock_actions(plan_data: dict, profile: dict | None = None) -> list[dict]:
    """Backward-compatible helper used by older main.py code."""
    profile = profile or {}
    steps = plan_data.get("steps") or []
    if not steps:
        return []
    target_step = None
    for step in steps:
        text = f"{step.get('name', '')} {step.get('meta', '')}"
        if _contains_any(text, ["餐", "锅", "咖啡", "海鲜", "轻食", "晚餐", "小吃"]):
            target_step = step
            break
    target_step = target_step or steps[-1]
    return [
        mock_reserve(target_step.get("name", ""), time=target_step.get("time", "17:30"), people_count=_people_count(profile)),
        mock_create_order(),
    ]
