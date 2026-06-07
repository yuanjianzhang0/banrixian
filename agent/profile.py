# -*- coding: utf-8 -*-
"""User profile analysis for the Banrixian planning agent."""

from __future__ import annotations

import json
import re
from typing import Any


CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "俩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def safe_json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    return CN_NUMBERS.get(value)


def _detect_city(text: str, current_user: dict | None) -> str:
    current_user = current_user or {}
    for city in ["北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "重庆", "天津"]:
        if city in text:
            return city
    return current_user.get("city") or "北京"


def _detect_child_age(text: str) -> str:
    patterns = [
        r"孩子\s*([0-9一二两俩三四五六七八九十]+)\s*岁",
        r"小孩\s*([0-9一二两俩三四五六七八九十]+)\s*岁",
        r"宝宝\s*([0-9一二两俩三四五六七八九十]+)\s*岁",
        r"([0-9一二两俩三四五六七八九十]+)\s*岁\s*(?:孩子|小孩|宝宝|儿子|女儿)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            age = _to_int(match.group(1))
            return f"{age}岁" if age is not None else f"{match.group(1)}岁"
    return ""


def _detect_time(text: str) -> str:
    day = ""
    if "今天" in text:
        day = "今天"
    elif "明天" in text:
        day = "明天"
    elif "周末" in text:
        day = "周末"

    period = ""
    if "上午" in text:
        period = "上午"
    elif "中午" in text:
        period = "中午"
    elif "下午" in text:
        period = "下午"
    elif "晚上" in text or "夜里" in text:
        period = "晚上"

    hour_match = re.search(r"(上午|中午|下午|晚上)?\s*([0-9一二两俩三四五六七八九十]+)\s*点", text)
    if hour_match:
        matched_period = hour_match.group(1) or period
        raw_number = hour_match.group(2)
        before = text[max(0, hour_match.start() - 3):hour_match.start()]
        ambiguous_little = raw_number == "一" and not hour_match.group(1) and not any(k in before for k in ["上午", "中午", "下午", "晚上"])
        if not ambiguous_little:
            hour = _to_int(raw_number)
            return f"{day}{matched_period}{hour}点".strip()
    if day or period:
        return f"{day}{period}".strip()
    return "未明确"


def _detect_route_style(text: str) -> str:
    text = text or ""
    style_patterns = [
        ("亲子", ["亲子", "孩子", "儿童", "小孩"]),
        ("约会", ["约会", "情侣", "浪漫", "老婆", "女朋友", "男朋友", "伴侣"]),
        ("朋友", ["朋友", "聚会", "同学", "同事"]),
        ("商务", ["商务", "客户", "宴请", "应酬"]),
        ("美食", ["美食", "吃饭", "晚餐", "午餐", "餐厅", "聚餐", "小吃"]),
        ("拍照", ["拍照", "打卡", "出片", "网红"]),
        ("室内", ["室内", "下雨", "太晒", "太热", "太冷"]),
        ("自然", ["自然", "公园", "散步", "户外", "游船"]),
        ("文化", ["文化", "博物馆", "艺术", "展览", "历史", "胡同"]),
        ("省钱", ["预算低", "便宜", "划算", "省钱", "免费"]),
        ("高端", ["高端", "品质", "贵一点", "不差钱"]),
        ("轻松", ["休闲", "放松", "不累", "轻松", "慢节奏"]),
    ]
    for style, keywords in style_patterns:
        if any(keyword in text for keyword in keywords):
            return style
    return "均衡"


def _detect_people(text: str) -> dict:
    adults = 1
    children = 0
    labels: list[str] = ["用户本人"]

    total_match = re.search(r"([0-9一二两俩三四五六七八九十]+)\s*个?\s*人", text)
    if total_match:
        total = _to_int(total_match.group(1)) or adults
        adults = max(adults, total)
        labels = [f"{total}人同行"]

    gender_match = re.search(r"([0-9一二两俩三四五六七八九十]+)\s*个?\s*男(?:生|士)?\s*([0-9一二两俩三四五六七八九十]+)\s*个?\s*女", text)
    if gender_match:
        male_count = _to_int(gender_match.group(1)) or 0
        female_count = _to_int(gender_match.group(2)) or 0
        total = male_count + female_count
        if total:
            adults = max(adults, total)
            labels = [f"{male_count}男{female_count}女"]

    if any(k in text for k in ["老婆", "妻子", "爱人", "女朋友", "对象", "伴侣", "老公", "丈夫", "男朋友", "约会"]):
        if not total_match and not gender_match:
            adults += 1
            labels.append("伴侣")

    if any(k in text for k in ["父母", "爸妈"]):
        adults += 2
        labels.append("父母")
    elif any(k in text for k in ["老人", "长辈", "爸爸", "妈妈", "爷爷", "奶奶"]):
        adults += 1
        labels.append("长辈")

    if any(k in text for k in ["孩子", "小孩", "宝宝", "儿子", "女儿", "亲子"]):
        children = max(children, 1)
        labels.append("孩子")

    friend_match = re.search(r"([0-9一二两俩三四五六七八九十]+)\s*个?\s*(?:朋友|同学|同事)", text)
    if friend_match:
        friend_count = _to_int(friend_match.group(1)) or 1
        if not total_match and not gender_match:
            adults += friend_count
            labels.append(f"{friend_count}个朋友")
    elif any(k in text for k in ["朋友", "同学", "同事"]) and not total_match and not gender_match:
        adults += 1
        labels.append("朋友")

    if any(k in text for k in ["客户", "商务", "宴请", "应酬"]):
        adults = max(adults, 2)
        labels.append("商务对象")

    if _looks_like_solo_trip(text) and not total_match and not gender_match:
        adults = 1
        children = 0
        labels = ["用户本人"]

    age = _detect_child_age(text)
    description = "、".join(labels)
    if age:
        description += f"，孩子{age}"

    return {
        "adults": adults,
        "children": children,
        "description": description,
        "child_age": age,
    }


def _looks_like_solo_trip(text: str) -> bool:
    if any(k in text for k in ["独自", "自己去", "我自己"]):
        return True
    if "一个人" in text and not any(k in text for k in ["还有一个人", "有一个人", "其中一个人", "另一个人", "一个人不"]):
        return True
    return False


def _relation_relevant(text: str, relation: str) -> bool:
    relation = relation or ""
    if not relation:
        return False
    groups = {
        "child": ["孩子", "小孩", "宝宝", "儿子", "女儿", "亲子", "带娃", "娃"],
        "elder": ["老人", "父母", "爸妈", "爸爸", "妈妈", "爷爷", "奶奶", "长辈", "腿脚"],
        "partner": ["老婆", "妻子", "爱人", "女朋友", "对象", "伴侣", "老公", "丈夫", "男朋友", "约会"],
        "friend": ["朋友", "同学", "同事", "聚会"],
    }
    relation_map = {
        "孩子": "child", "儿子": "child", "女儿": "child", "宝宝": "child",
        "父母": "elder", "爸爸": "elder", "妈妈": "elder", "爷爷": "elder", "奶奶": "elder", "老人": "elder",
        "老婆": "partner", "妻子": "partner", "老公": "partner", "丈夫": "partner", "伴侣": "partner", "女朋友": "partner", "男朋友": "partner",
        "朋友": "friend", "同学": "friend", "同事": "friend",
    }
    group = next((value for key, value in relation_map.items() if key in relation), "")
    if group:
        return any(word in text for word in groups[group])
    return relation in text


def _is_time_or_stale_memory(title: str) -> bool:
    return any(k in title for k in ["今天", "明天", "后天", "周末", "上午", "下午", "晚上", "时间偏好", "出发"])


def _memory_needs_relation(text: str, title: str) -> bool:
    relation_words = {
        "child": ["孩子", "小孩", "宝宝", "儿子", "女儿", "亲子", "儿童"],
        "elder": ["父母", "爸妈", "爸爸", "妈妈", "爷爷", "奶奶", "老人", "长辈"],
    }
    if any(word in title for word in relation_words["child"]):
        return any(word in text for word in relation_words["child"])
    if any(word in title for word in relation_words["elder"]):
        return any(word in text for word in relation_words["elder"])
    return True


def build_user_profile(
    text: str,
    current_user: dict | None = None,
    family_members: list | None = None,
    preference_memory: list | None = None,
) -> dict:
    """Build a display-safe profile from user text and optional user context."""
    text = text or ""
    current_user = current_user or {}
    family_members = family_members or []
    preference_memory = preference_memory or []

    scene: list[str] = []
    preferences: list[str] = []
    constraints: list[str] = []
    budget: list[str] = []

    if any(k in text for k in ["孩子", "小孩", "宝宝", "儿子", "女儿", "亲子"]):
        scene.append("亲子出行")
        preferences.extend(["亲子友好", "安全性高", "动线不要太累"])

    if any(k in text for k in ["老婆", "妻子", "爱人", "女朋友", "对象", "伴侣", "老公", "丈夫", "男朋友", "约会"]):
        scene.append("伴侣同行")
        preferences.extend(["环境舒适", "适合拍照", "体验感好"])

    if any(k in text for k in ["朋友", "同学", "同事", "聚会"]):
        scene.append("朋友聚会")
        preferences.extend(["适合多人同行", "方便聊天", "互动性强"])

    if any(k in text for k in ["客户", "商务", "宴请", "应酬"]):
        scene.append("商务宴请")
        preferences.extend(["环境体面", "服务稳定", "适合正式沟通"])

    if _looks_like_solo_trip(text):
        scene.append("独自休闲")
        preferences.extend(["节奏轻松", "适合放松"])

    if any(k in text for k in ["美食", "吃饭", "晚餐", "午餐", "餐厅", "聚餐"]):
        preferences.append("美食")
    if any(k in text for k in ["休闲", "放松", "不累", "别太累", "不太累", "散步"]):
        preferences.append("休闲")
    if any(k in text for k in ["拍照", "打卡", "出片"]):
        preferences.append("适合拍照")

    time_preference = _detect_time(text)
    route_style = _detect_route_style(text)
    if time_preference != "未明确":
        constraints.append(f"{time_preference}出发" if "点" in time_preference else f"时间偏好：{time_preference}")

    if any(k in text for k in ["别太远", "不要太远", "附近", "离家近", "近一点", "别跑太远"]):
        constraints.append("距离不要太远")

    if any(k in text for k in ["减肥", "低卡", "清淡", "少油", "控糖", "不胖", "忌口", "不吃辣", "不能吃辣", "不太能吃辣"]):
        constraints.append("饮食低负担")
        preferences.extend(["低油低负担", "可选择清淡菜品"])
    if any(k in text for k in ["不吃辣", "不能吃辣", "不太能吃辣"]):
        constraints.append("避免重辣")

    if any(k in text for k in ["老人", "爸妈", "父母", "腿脚"]):
        constraints.append("照顾老人，避免长时间步行和排队")

    if any(k in text for k in ["预算低", "便宜", "划算", "省钱"]):
        budget.append("偏经济")
        constraints.append("预算偏经济")
    elif any(k in text for k in ["高端", "品质", "贵一点", "不差钱"]):
        budget.append("偏品质")

    # 亲友画像：只把本次明确提到的亲友融入偏好和约束，避免历史画像污染朋友/约会等场景。
    for member in family_members:
        if not isinstance(member, dict):
            continue
        relation = str(member.get("relation") or "")
        if not _relation_relevant(text, relation):
            continue
        tags = member.get("tags") or []
        if isinstance(tags, str):
            try:
                import json as _json
                tags = _json.loads(tags)
            except Exception:
                tags = [tags]
        for tag in tags:
            tag = str(tag).strip()
            if not tag:
                continue
            if any(k in tag for k in ["拍照", "出片", "打卡"]):
                preferences.append("适合拍照")
            if any(k in tag for k in ["忌口", "不吃", "过敏", "香菜"]):
                label = f"{relation}忌口：{tag}" if relation else tag
                constraints.append(label)
            if any(k in tag for k in ["清淡", "低脂", "低卡", "少油", "减肥"]):
                preferences.append("饮食低负担")
                if relation:
                    constraints.append(f"{relation}偏好清淡饮食")
            if any(k in tag for k in ["腿脚", "久站", "久走", "老人"]):
                label = f"照顾{relation}，避免长时间步行和排队" if relation else "避免长时间步行和排队"
                constraints.append(label)
            if any(k in tag for k in ["亲子", "儿童", "孩子"]):
                scene.append("亲子出行")
                preferences.append("亲子友好")

    # 长期记忆：把 preference_memory 融入偏好和约束
    memory_preference_titles = {
        "饮食低负担", "亲子友好", "适合多人同行", "偏好室内备选", "低油低负担", "可选择清淡菜品"
    }
    memory_constraint_titles = {
        "距离不要太远", "饮食忌口需确认"
    }
    for mem in preference_memory:
        if not isinstance(mem, dict):
            continue
        title = str(mem.get("title") or "").strip()
        mem_type = str(mem.get("memory_type") or "")
        if not title:
            continue
        if _is_time_or_stale_memory(title):
            continue
        if not _memory_needs_relation(text, title):
            continue
        if mem_type == "preference":
            if title in memory_preference_titles:
                preferences.append(title)
            elif title in memory_constraint_titles or "距离" in title or "室内" in title:
                constraints.append(title)
            elif "预算" in title or "经济" in title:
                budget.append("偏经济")
                constraints.append(title)
            else:
                preferences.append(title)
        elif mem_type == "family_preference":
            relation = str(mem.get("relation") or "")
            if _relation_relevant(text, relation) or any(word in text for word in title.split()):
                constraints.append(title)

    if not scene:
        scene.append("本地生活规划")
    if not preferences:
        preferences.append("轻松省心")
    if not constraints:
        constraints.append("无明确硬约束，默认优先省时和体验稳定")

    def _opt_float(v: Any) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "scene": unique(scene),
        "people": _detect_people(text),
        "preferences": unique(preferences),
        "constraints": unique(constraints),
        "city": _detect_city(text, current_user),
        "time_preference": time_preference,
        "route_style": route_style,
        "budget": unique(budget),
        "user_tags": safe_json_loads(current_user.get("tags"), []),
        "family_members": family_members,
        "raw_text": text,
        "user_lat": _opt_float(current_user.get("lat")),
        "user_lng": _opt_float(current_user.get("lng")),
        "user_district": str(current_user.get("district") or "").strip() or None,
    }
