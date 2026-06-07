# -*- coding: utf-8 -*-
"""Run deterministic route-planning quality checks and write a markdown log."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.profile import build_user_profile
from agent.harness import _choose_search_keyword
from agent.tools import (
    build_final_plan,
    decompose_goal,
    plan_time_slots,
    rank_places_for_plan,
    search_places,
)
from core.database import get_db


DEFAULT_LOG = ROOT / "test_logs" / "route_planning_quality_20260602.md"
FIXED_NOW = {
    "timezone": "Asia/Beijing",
    "now_iso": "2026-06-02T16:30+08:00",
    "date": "2026-06-02",
    "weekday": "周二",
    "hour": 16,
    "minute": 30,
    "period": "afternoon",
    "readable": "2026-06-02 周二 16:30",
}


CASES: list[dict[str, Any]] = [
    {"id": "meal_01", "text": "今晚帮我安排一个晚饭，想吃火锅", "expect_keyword": "火锅", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_02", "text": "北京今晚吃烤鸭推荐一家就行", "keyword": "烤鸭", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_03", "text": "中午找个适合商务午餐的餐厅", "keyword": "商务", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_04", "text": "明天午饭带父母吃清淡一点", "keyword": "清淡", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_05", "text": "晚上和朋友聚餐，推荐一家餐厅", "keyword": "聚会", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_06", "text": "下午找一个咖啡馆聊事情", "keyword": "咖啡", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_07", "text": "今晚订个茶馆，安静一点", "keyword": "茶馆", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_08", "text": "找一家适合约会的晚餐", "keyword": "约会", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_09", "text": "午餐想吃海鲜，不要太远", "keyword": "海鲜", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "meal_10", "text": "今晚一个人吃饭，便宜点", "keyword": "省钱", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "short_01", "text": "现在有一个小时，安排个轻松活动", "keyword": "休闲", "expect_max_steps": 1},
    {"id": "short_02", "text": "两个小时亲子游，孩子5岁", "keyword": "亲子", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "short_03", "text": "今晚两小时约会路线，室内一点", "keyword": "室内", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "short_04", "text": "明天上午两个小时带父母逛逛", "keyword": "文化", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "short_05", "text": "下午三点到五点，安排拍照路线", "keyword": "拍照", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "short_06", "text": "下雨天两小时室内亲子活动", "keyword": "室内", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "short_07", "text": "一小时想带孩子放电", "keyword": "孩子", "expect_max_steps": 1},
    {"id": "short_08", "text": "一小时约会喝咖啡", "keyword": "咖啡", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "morning_01", "text": "明天上午带爸妈轻松逛一下", "keyword": "文化", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "morning_02", "text": "周末上午亲子游，不要太累", "keyword": "亲子", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "morning_03", "text": "明天上午自然风格路线", "keyword": "自然", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "morning_04", "text": "上午拍照打卡，两个小时", "keyword": "拍照", "expect_max_steps": 2},
    {"id": "morning_05", "text": "上午和朋友逛逛再喝咖啡", "keyword": "朋友", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "afternoon_01", "text": "今天下午帮我安排一个半日路线", "keyword": "休闲", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "afternoon_02", "text": "周末下午约会，想要室内有氛围", "keyword": "室内", "expect_max_steps": 4, "expect_max_food": 2},
    {"id": "afternoon_03", "text": "下午亲子半日游，孩子喜欢恐龙", "keyword": "亲子", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "afternoon_04", "text": "下午带父母文化路线，腿脚一般", "keyword": "文化", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "afternoon_05", "text": "下午朋友小聚，吃吃逛逛", "keyword": "朋友", "expect_max_steps": 4, "expect_max_food": 2},
    {"id": "afternoon_06", "text": "下午省钱路线，适合学生", "keyword": "省钱", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "afternoon_07", "text": "下午自然散步路线，不要太晒", "keyword": "自然", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "evening_01", "text": "今晚约会路线，先逛再吃饭", "keyword": "约会", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "evening_02", "text": "今晚三小时朋友路线，可以吃饭", "keyword": "朋友", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "evening_03", "text": "晚上带孩子室内玩两个小时", "keyword": "室内", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "evening_04", "text": "晚上带父母散步加晚饭", "keyword": "自然", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "evening_05", "text": "晚上一个半小时轻松路线", "keyword": "轻松", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "full_01", "text": "周末一天亲子游，别全是游乐场", "keyword": "亲子", "expect_max_steps": 5, "expect_max_food": 2, "expect_max_playground": 2},
    {"id": "full_02", "text": "明天全天带爸妈北京文化休闲", "keyword": "文化", "expect_max_steps": 5, "expect_max_food": 2, "expect_max_playground": 0},
    {"id": "full_03", "text": "周末一天美食路线，但不要一直吃", "keyword": "美食", "expect_max_steps": 5, "expect_max_food": 2, "expect_min_non_food": 1},
    {"id": "full_04", "text": "全天自然路线，穿插吃饭休息", "keyword": "自然", "expect_max_steps": 5, "expect_max_food": 2},
    {"id": "full_05", "text": "明天一天约会路线，有艺术和晚餐", "keyword": "约会", "expect_max_steps": 5, "expect_max_food": 2},
    {"id": "family_01", "text": "我有一个新的儿子，周末带他玩两小时", "keyword": "亲子", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "family_02", "text": "没有女儿，但想给女儿型亲子路线做参考", "keyword": "亲子", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "family_03", "text": "带爷爷奶奶上午逛逛，别走太多路", "keyword": "文化", "expect_max_steps": 2, "expect_max_food": 1, "expect_max_playground": 0},
    {"id": "family_04", "text": "和老婆晚上约会吃饭，只要一家", "keyword": "约会", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "family_05", "text": "和三个朋友下午聚会路线，三小时", "keyword": "朋友", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "style_01", "text": "拍照路线两个小时，不要都在餐厅", "keyword": "拍照", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "style_02", "text": "室内路线半天，天气太热", "keyword": "室内", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "style_03", "text": "省钱路线一下午，最好免费", "keyword": "省钱", "expect_max_steps": 4, "expect_max_food": 1},
    {"id": "style_04", "text": "高端商务晚上宴请", "keyword": "商务", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "style_05", "text": "文化路线三小时，想看展", "keyword": "文化", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "real_01", "text": "北京下午半日亲子自然路线", "keyword": "亲子", "expect_max_steps": 4, "expect_max_food": 1, "expect_max_playground": 2},
    {"id": "real_02", "text": "北京今晚晚饭推荐", "keyword": "餐厅", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "real_03", "text": "北京明天上午老人友好路线", "keyword": "文化", "expect_max_steps": 2, "expect_max_food": 1, "expect_max_playground": 0},
    {"id": "real_04", "text": "北京周末下午情侣室内路线", "keyword": "室内", "expect_max_steps": 4, "expect_max_food": 2},
    {"id": "real_05", "text": "北京两个小时美食路线，不要多个饭馆", "keyword": "美食", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "real_06", "text": "北京一上午亲子科普路线", "keyword": "亲子", "expect_max_steps": 2, "expect_max_food": 1},
    {"id": "regression_01", "text": "周末约会，下午3点开始，想看展或逛公园", "keyword": "文化", "expect_max_steps": 4, "expect_max_food": 1, "expect_min_non_food": 1, "expect_activity": True},
    {"id": "regression_02", "text": "周末约会，上午9点开始，想看展或逛公园", "keyword": "文化", "expect_max_steps": 3, "expect_max_food": 1, "expect_min_food": 1, "expect_min_non_food": 1, "expect_activity": True},
    {"id": "regression_03", "text": "下午3点到5点，想看展或逛公园，不吃饭", "keyword": "文化", "expect_max_steps": 2, "expect_max_food": 0, "expect_min_non_food": 1, "expect_activity": True},
    {"id": "regression_04", "text": "今晚只想约会吃饭，给一家餐厅就行", "keyword": "约会", "expect_max_steps": 1, "expect_max_food": 1},
    {"id": "regression_05", "text": "一上午老人友好路线，别一直吃东西", "keyword": "文化", "expect_max_steps": 3, "expect_max_food": 1, "expect_min_non_food": 1, "expect_activity": True},
    {
        "id": "memory_01",
        "text": "今晚帮我安排一个晚饭，想吃火锅",
        "expect_keyword": "火锅",
        "expect_max_steps": 1,
        "expect_max_food": 1,
        "preference_memory": [
            {"memory_type": "preference", "title": "亲子友好"},
            {"memory_type": "preference", "title": "父母偏好清淡饮食"},
            {"memory_type": "family_preference", "title": "孩子需要亲子友好", "relation": "孩子"},
        ],
        "forbidden_preferences": ["亲子友好", "父母偏好清淡饮食"],
        "forbidden_constraints": ["孩子需要亲子友好"],
    },
    {
        "id": "memory_02",
        "text": "两个小时亲子游，孩子5岁",
        "keyword": "亲子",
        "expect_max_steps": 2,
        "expect_max_food": 1,
        "preference_memory": [
            {"memory_type": "preference", "title": "父母偏好清淡饮食"},
            {"memory_type": "family_preference", "title": "父母出行照顾", "relation": "父母"},
        ],
        "forbidden_preferences": ["父母偏好清淡饮食"],
        "forbidden_constraints": ["父母出行照顾"],
    },
]


def load_places(city: str = "北京") -> list[dict[str, Any]]:
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.name, p.category, COALESCE(p.tags, p.keyword, '') AS keyword,
                       p.address, p.open_hours, p.price_range, p.score, p.desc_text,
                       p.lng, p.lat, p.source, p.source_id, p.phone, p.city,
                       cs.available AS available_seats, cs.queue_count,
                       COALESCE(cs.status, 'available') AS capacity_status
                FROM places p
                LEFT JOIN place_capacity_status cs ON cs.place_id = p.id
                WHERE p.city = %s
                ORDER BY p.source = 'amap' DESC, p.score DESC, p.id DESC
                """,
                (city,),
            )
            return list(cursor.fetchall())
    finally:
        conn.close()


def count_food(steps: list[dict[str, Any]]) -> int:
    return sum(1 for step in steps if any(word in f"{step.get('name', '')} {step.get('meta', '')} {step.get('category', '')}" for word in ["餐", "咖啡", "茶", "火锅", "烤鸭", "饭", "酒吧"]))


def count_playground(steps: list[dict[str, Any]]) -> int:
    return sum(1 for step in steps if any(word in f"{step.get('name', '')} {step.get('meta', '')}" for word in ["乐园", "游乐", "亲子", "儿童"]))


def no_meal_request(text: str) -> bool:
    return any(word in str(text or "") for word in ["不吃饭", "不用吃饭", "不安排吃饭", "不要吃饭", "无需吃饭", "不吃东西", "不安排餐厅", "不要餐厅", "不考虑吃饭"])


def minutes_between(start: str, end: str) -> int:
    def minute(value: str) -> int:
        m = re.match(r"^(\d{1,2}):(\d{2})$", value or "")
        if not m:
            return 0
        return int(m.group(1)) * 60 + int(m.group(2))

    s = minute(start)
    e = minute(end)
    if e <= s:
        e += 24 * 60
    return max(0, e - s)


def run_case(case: dict[str, Any], places: list[dict[str, Any]]) -> dict[str, Any]:
    random.seed(case["id"])
    text = case["text"]
    current_user = {"city": "北京", "tags": []}
    profile = build_user_profile(text, current_user=current_user, family_members=[], preference_memory=case.get("preference_memory") or [])
    goal = decompose_goal(text, current_user=current_user)
    time_plan = plan_time_slots(text=text, current_time=FIXED_NOW, goal=goal)
    keyword = case.get("keyword") or _choose_search_keyword(text, profile, goal)
    search_result = search_places(keyword, limit=20, places=places)
    rank_result = rank_places_for_plan(
        profile,
        places=search_result.get("places") or [],
        limit=20,
        current_time=FIXED_NOW,
        time_plan=time_plan,
        recently_used_names=set(),
    )
    plan = build_final_plan(
        profile,
        ranked_places=rank_result.get("ranked_places") or [],
        current_time=FIXED_NOW,
        goal=goal,
        time_plan=time_plan,
        risks=rank_result.get("risks") or [],
        original_text=text,
    )
    steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    violations: list[str] = []
    max_steps = int(case.get("expect_max_steps") or 5)
    max_food = int(case.get("expect_max_food") if case.get("expect_max_food") is not None else max_steps)
    food_intent_words = ["吃", "晚饭", "晚餐", "午饭", "午餐", "早餐", "夜宵", "餐厅", "饭馆", "火锅", "烤鸭", "咖啡", "茶馆", "喝茶", "美食", "聚餐", "宴请"]
    min_food = int(case.get("expect_min_food") if case.get("expect_min_food") is not None else (0 if no_meal_request(case["text"]) else (1 if any(word in case["text"] for word in food_intent_words) else 0)))
    max_playground = int(case.get("expect_max_playground") if case.get("expect_max_playground") is not None else 2)
    min_non_food = int(case.get("expect_min_non_food") or 0)
    food_count = count_food(steps)
    playground_count = count_playground(steps)
    non_food_count = len(steps) - food_count
    duration = minutes_between(time_plan.get("start", ""), time_plan.get("end", ""))

    if len(steps) > max_steps:
        violations.append(f"步数过多：{len(steps)} > {max_steps}")
    if food_count > max_food:
        violations.append(f"餐饮点过多：{food_count} > {max_food}")
    if food_count < min_food:
        violations.append(f"缺少餐饮点：{food_count} < {min_food}")
    if non_food_count < min_non_food:
        violations.append(f"非餐饮点不足：{non_food_count} < {min_non_food}")
    if playground_count > max_playground:
        violations.append(f"乐园/亲子点过多：{playground_count} > {max_playground}")
    if case.get("expect_keyword") and keyword != case.get("expect_keyword"):
        violations.append(f"关键词选择错误：{keyword} != {case.get('expect_keyword')}")
    profile_preferences = [str(item) for item in profile.get("preferences") or []]
    profile_constraints = [str(item) for item in profile.get("constraints") or []]
    for forbidden in [str(item) for item in case.get("forbidden_preferences") or []]:
        if forbidden in profile_preferences:
            violations.append(f"记忆污染偏好：{forbidden}")
    for forbidden in [str(item) for item in case.get("forbidden_constraints") or []]:
        if forbidden in profile_constraints:
            violations.append(f"记忆污染约束：{forbidden}")
    if duration <= 120 and len(steps) > 2:
        violations.append(f"短时长路线过满：{duration}分钟却有{len(steps)}站")
    if duration <= 90 and len(steps) > 1:
        violations.append(f"一小时级路线过满：{duration}分钟却有{len(steps)}站")
    if not steps:
        violations.append("没有生成任何真实地点")
    names = [str(step.get("name") or "") for step in steps]
    if len(names) != len(set(names)):
        violations.append("路线出现重复地点")
    if food_count == len(steps) and len(steps) >= 3:
        violations.append("路线几乎全是吃喝")
    if case.get("expect_activity") and food_count == len(steps):
        violations.append("活动诉求却全是餐饮点")
    if playground_count == len(steps) and len(steps) >= 2:
        violations.append("路线全是游乐/亲子场所")

    return {
        "case": case,
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "time_plan": time_plan,
        "steps": steps,
        "food_count": food_count,
        "playground_count": playground_count,
        "keyword": keyword,
        "candidate_count": len(plan.get("candidate_plans") or []),
        "intro": plan.get("intro") or "",
        "thinking": plan.get("thinking") or [],
    }


def write_log(results: list[dict[str, Any]], log_path: Path) -> None:
    passed = sum(1 for item in results if item["status"] == "PASS")
    failed = len(results) - passed
    lines: list[str] = []
    lines.append("# Route Planning Quality Test Log")
    lines.append("")
    lines.append(f"- generated_at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- fixed_current_time: {FIXED_NOW['readable']}")
    lines.append(f"- total_cases: {len(results)}")
    lines.append(f"- passed: {passed}")
    lines.append(f"- failed: {failed}")
    lines.append("- execution_mode: deterministic_python_toolchain")
    lines.append("- real_llm_calls: 0")
    lines.append("- local_planner_cases: " + str(len(results)))
    lines.append("- note: 本脚本直接调用 Python 规划工具链，不经过 AgentHarness 的真实 LLM 选择环节；真实接口会在 status 事件中输出 provider/model。")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| id | status | request | time | steps | food | playground | violations |")
    lines.append("|---|---|---|---|---:|---:|---:|---|")
    for item in results:
        tp = item["time_plan"]
        steps = item["steps"]
        violation_text = "；".join(item["violations"]) if item["violations"] else "-"
        lines.append(
            f"| {item['case']['id']} | {item['status']} | {item['case']['text']} | "
            f"{tp.get('date')} {tp.get('start')}-{tp.get('end')} | {len(steps)} | "
            f"{item['food_count']} | {item['playground_count']} | {violation_text} |"
        )
    lines.append("")
    lines.append("## Details")
    for item in results:
        case = item["case"]
        lines.append("")
        lines.append(f"### {case['id']} {item['status']}")
        lines.append("")
        lines.append(f"- request: {case['text']}")
        lines.append(f"- keyword: {item.get('keyword', case.get('keyword', ''))}")
        lines.append(f"- time_plan: `{json.dumps(item['time_plan'], ensure_ascii=False)}`")
        lines.append(f"- intro: {item['intro']}")
        if item["violations"]:
            lines.append(f"- violations: {'；'.join(item['violations'])}")
        else:
            lines.append("- violations: none")
        lines.append("- steps:")
        for index, step in enumerate(item["steps"], 1):
            meta = str(step.get("meta") or "").replace("\n", " ")[:120]
            lines.append(f"  {index}. {step.get('time')} {step.get('name')} | {meta}")
        if item["thinking"]:
            lines.append("- thinking:")
            for thought in item["thinking"][:6]:
                lines.append(f"  - {str(thought).replace(chr(10), ' ')[:160]}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--city", default="北京")
    args = parser.parse_args()

    places = load_places(args.city)
    results = [run_case(case, places) for case in CASES]
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    write_log(results, log_path)
    failed = [item for item in results if item["status"] != "PASS"]
    print(json.dumps({
        "log": str(log_path),
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "failed_ids": [item["case"]["id"] for item in failed],
    }, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
