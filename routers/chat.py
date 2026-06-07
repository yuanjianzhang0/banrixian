# -*- coding: utf-8 -*-
"""Chat and planning routes."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from agent.llm_client import create_llm_client
from agent.memory_store import (
    _load_family_memory_for_user,
    _load_preference_memory_for_user,
    _save_preference_memories,
)
from agent.planner import run_agent_stream
from core.auth import get_current_user
from core.database import get_db
from core.session import (
    _append_session_history,
    _get_session_history,
)
from core.sse import (
    done_event,
    error_event,
    intent_event,
    observation_event,
    skill_call_event,
    sse_format,
    status_event,
)
from skills.weather import get_weather


logger = logging.getLogger("BanrixianAPI")

router = APIRouter(prefix="/v1", tags=["chat", "agent"])

SUPPORTED_CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "重庆", "天津"]
ON_DEMAND_AMAP_MIN_PLACES = int(os.getenv("ON_DEMAND_AMAP_MIN_PLACES", "30"))
ON_DEMAND_AMAP_PAGES = int(os.getenv("ON_DEMAND_AMAP_PAGES", "1"))
ON_DEMAND_AMAP_OFFSET = int(os.getenv("ON_DEMAND_AMAP_OFFSET", "12"))


def _normalize_city(city: str | None) -> str:
    raw = str(city or "").strip()
    if not raw:
        return "北京"
    raw = raw[:-1] if raw.endswith("市") else raw
    return raw or "北京"


def _resolve_request_city(text: str, payload_city: str | None = None, current_user: dict | None = None) -> str:
    if str(payload_city or "").strip():
        return _normalize_city(payload_city)
    text = text or ""
    for city in SUPPORTED_CITIES:
        if city in text or f"{city}市" in text:
            return city
    return _normalize_city((current_user or {}).get("city") or "北京")


def _strip_code_fence(raw: str) -> str:
    """清理大模型可能返回的 Markdown 代码块。"""
    raw = (raw or "").strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return raw


def _parse_json_object(raw: str) -> dict:
    """
    尽量从大模型输出中解析 JSON。
    商用场景不能因为模型多输出几个字就让接口崩。
    """
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _is_agent_tool_decision(data: dict | None) -> bool:
    return isinstance(data, dict) and data.get("type") == "tool_call" and data.get("tool")


_RELATION_WORDS = ["老婆", "老公", "妻子", "丈夫", "孩子", "儿子", "女儿", "宝宝",
                   "父母", "爸爸", "妈妈", "爷爷", "奶奶", "朋友", "闺蜜", "好友"]
_INFO_WORDS = ["正在", "喜欢", "爱好", "爱看", "爱玩", "上学", "上班", "岁",
               "爱", "怕", "不吃", "忌口", "过敏", "减肥", "清淡", "腿脚"]

_STRONG_PLAN_WORDS = [
    "安排", "规划", "路线", "行程", "帮我", "帮忙",
    "去哪", "景点", "餐厅", "公园", "咖啡", "博物馆", "展览",
    "预约", "下单", "出行", "游玩", "出去玩", "逛逛", "逛街",
    "玩", "转转", "走走", "吃饭", "吃点", "喝咖啡",
    "半日", "今天下午", "今天上午", "今晚", "明天", "周末",
    "下午", "上午", "晚上",
    "和老婆", "和老公", "带孩子", "和孩子", "带娃", "亲子",
    "和朋友", "和父母", "带父母", "和家人",
    "推荐", "推荐地方", "哪里好", "去哪里", "去哪儿",
]
_NO_PLAN_WORDS = [
    "不要规划", "不用规划", "别规划", "先别规划", "不规划",
    "不要安排", "不用安排", "先聊天", "先聊一句", "只聊天",
]


def _has_no_plan_signal(text: str) -> bool:
    return any(k in (text or "") for k in _NO_PLAN_WORDS)


def _has_strong_plan_signal(text: str) -> bool:
    text = text or ""
    return len(text) > 3 and any(k in text for k in _STRONG_PLAN_WORDS)


def _local_chat_reply(text: str) -> str:
    text = (text or "").strip()
    if any(word in text for word in ["你好", "您好", "hello", "hi"]):
        return "你好！我是半日闲AI，可以帮你规划本地出行路线。告诉我想去哪、带谁、什么时间，我来安排。"
    if any(word in text for word in ["你是谁", "你能干什么", "介绍一下", "怎么用"]):
        return "我是半日闲AI，一句话帮你安排半日出行路线，考虑时间、同行人、饮食和距离。直接说你的需求就好。"

    # 检测亲友信息分享
    matched_relation = next((r for r in _RELATION_WORDS if r in text), None)
    if matched_relation and any(w in text for w in _INFO_WORDS):
        return f"好的，{matched_relation}的情况我记下来了，下次帮你规划时会一并考虑。有想去哪儿的打算吗？"

    # 单纯分享个人情况
    if any(w in text for w in _INFO_WORDS):
        return "嗯，记下来了！有出行需求随时说，我会结合你分享的信息帮你安排路线。"

    return "我在呢，告诉我出发时间、同行人和偏好，帮你安排一条顺路的路线。"


def _is_weather_request(text: str) -> bool:
    text = text or ""
    weather_words = [
        "天气", "下雨", "有雨", "雨天", "会下雨", "今天雨", "冷不冷", "热不热",
        "气温", "温度", "风大", "晒不晒", "适合户外", "适合出门",
    ]
    return any(word in text for word in weather_words)


def _resolve_weather_city(text: str, current_user: dict | None = None) -> str:
    text = text or ""
    for city in SUPPORTED_CITIES:
        if city in text:
            return city
    return _normalize_city((current_user or {}).get("city") or "北京")


def _weather_summary(result: dict) -> str:
    if not isinstance(result, dict):
        return "天气工具返回异常"
    return result.get("message") or ("已获取实时天气" if result.get("status") == "ok" else "实时天气暂不可用")


def _build_weather_done_data(result: dict, city: str) -> dict:
    if isinstance(result, dict) and result.get("status") == "ok":
        risks = result.get("outdoor_risk") or []
        risk_note = f"，户外提醒：{'、'.join(risks)}" if risks else "，暂未发现明显降雨或大风风险"
        content = f"{result.get('message') or f'{city}实时天气已获取'}{risk_note}。"
    else:
        message = result.get("message") if isinstance(result, dict) else "实时天气暂不可用"
        content = f"{message}。我不会编造实时天气，建议同时查看手机天气；如果要规划出行，我可以优先按室内或低风险路线安排。"
    return {
        "type": "text",
        "intent": "weather",
        "content": content,
        "weather": result if isinstance(result, dict) else {"status": "unavailable", "city": city},
    }


def _safe_user_for_prompt(user: dict | None) -> dict:
    """避免把密码等敏感字段发给模型。"""
    if not user:
        return {}
    blocked = {"password"}
    return {k: v for k, v in user.items() if k not in blocked}



def _history_has_plan(history: list | None) -> bool:
    history = history or []
    return any(
        item.get("role") == "assistant"
        and any(k in str(item.get("content", "")) for k in ["已生成规划", "已推荐", "路线", "行程"])
        for item in history
        if isinstance(item, dict)
    )


def _summarize_ai_output(data: dict | None) -> str:
    if not isinstance(data, dict):
        return ""
    if data.get("type") == "plan":
        names = [step.get("name") for step in (data.get("steps") or []) if isinstance(step, dict) and step.get("name")]
        if names:
            action_types = {action.get("type") for action in (data.get("actions") or []) if isinstance(action, dict)}
            reserve_note = "，包含模拟预约" if "reserve" in action_types else ""
            return f"已生成规划：{' + '.join(names[:4])}，约下午半日路线{reserve_note}。"
        return "已生成规划：暂无可用地点。"
    content = str(data.get("content") or "").strip()
    if data.get("intent") == "clarify":
        return "追问用户：" + content[:80]
    if content:
        return content[:100]
    return ""





async def answer_chat_by_llm(text: str, current_user: dict | None = None, history: list | None = None) -> str:
    """
    非规划类问题直接调用 .env 中配置的 LLM 回复。
    这里不进入复杂 Agent，降低耗时和出错率。
    """
    text = (text or "").strip()
    if not text:
        return "你好，我是半日闲AI。你可以告诉我出发时间、同行人和偏好，我来帮你安排本地半日行程。"

    system_prompt = (
        "你是半日闲AI，微信小程序里的本地出行规划助手，风格温暖、简洁、像朋友。\n\n"
        "回复规则：\n"
        "1. 用户分享家人/亲友信息（如儿子上小学、老婆喜欢奥特曼、爸妈腿不好）：\n"
        "   温暖确认记住了，说明下次规划会考虑进去，可追问出行计划。\n"
        "   示例：了解了！儿子喜欢奥特曼，这个记住了，下次安排亲子路线会找相关主题场馆。有想出去玩的计划吗？\n\n"
        "2. 用户分享个人偏好（如不吃辣、喜欢安静）：\n"
        "   自然记下，不废话。示例：不吃辣记住了，帮你找路线时会避开辣菜。\n\n"
        "3. 普通问候/产品问题：自然简短回复。\n\n"
        "4. 完全不相关话题：礼貌说超出范围。\n\n"
        "禁止：说我在呢之类无意义的话；输出JSON；自称OpenAI或华为；超过3句话。"
    )

    try:
        llm = create_llm_client()
        if getattr(llm, "provider_name", "") == "local":
            return _local_chat_reply(text)
        raw = await asyncio.wait_for(
            llm.complete([
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps({
                        "message": text,
                        "current_user": _safe_user_for_prompt(current_user),
                        "session_history": history or [],
                    }, ensure_ascii=False)
                }
            ]),
            timeout=float(os.getenv("CHAT_TIMEOUT_SECONDS", "15"))
        )
        parsed = _parse_json_object(raw)
        if _is_agent_tool_decision(parsed):
            logger.warning(f"普通聊天收到 Agent tool_call，已丢弃并使用本地回复: {raw}")
            return _local_chat_reply(text)
        return (raw or "").strip() or "我在呢，可以告诉我你想什么时候、和谁一起出门，我来帮你安排。"
    except Exception as e:
        logger.exception(f"普通聊天 LLM 回复失败: {e}")
        return _local_chat_reply(text)


def _places_has_city_column(cursor) -> bool:
    cursor.execute("SHOW COLUMNS FROM places LIKE 'city'")
    return cursor.fetchone() is not None


def _city_place_count(city: str) -> int:
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            if _places_has_city_column(cursor):
                cursor.execute("SELECT COUNT(*) AS n FROM places WHERE city = %s", (city,))
            else:
                cursor.execute("SELECT COUNT(*) AS n FROM places")
            row = cursor.fetchone() or {}
            return int(row.get("n") or 0)
    finally:
        conn.close()


def _amap_keywords_for_request(text: str) -> list[str]:
    text = str(text or "")
    keywords: list[str] = []

    def add(items: list[str]) -> None:
        for item in items:
            if item not in keywords:
                keywords.append(item)

    if any(word in text for word in ["吃", "餐", "饭", "火锅", "烤鸭", "咖啡", "茶", "美食", "约会"]):
        add(["餐厅", "咖啡", "茶馆", "火锅", "烤鸭"])
    if any(word in text for word in ["看展", "展览", "博物馆", "美术馆", "文化", "室内"]):
        add(["博物馆", "展览", "美术馆"])
    if any(word in text for word in ["公园", "自然", "散步", "逛", "景点", "户外"]):
        add(["公园", "景点", "商场"])
    if any(word in text for word in ["孩子", "亲子", "带娃", "儿子", "女儿"]):
        add(["亲子乐园", "游乐场", "博物馆", "公园"])
    add(["餐厅", "咖啡", "公园", "博物馆", "展览", "景点"])
    return keywords[:10]


def _ensure_city_places_from_amap(city: str, text: str) -> dict:
    city = _normalize_city(city)
    before = _city_place_count(city)
    if before >= ON_DEMAND_AMAP_MIN_PLACES:
        return {"status": "skipped", "city": city, "before": before, "inserted": 0, "updated": 0}

    try:
        from scripts.import_amap_pois import (
            _completed_runs,
            _fetch_page,
            _normalize_poi,
            _read_key,
            _record_import_run,
            _upsert_places,
        )
    except Exception as exc:
        logger.warning("高德按需补库模块加载失败 city=%s: %s", city, exc)
        return {"status": "failed", "city": city, "before": before, "error": str(exc)}

    keywords = _amap_keywords_for_request(text)
    pages = max(1, min(ON_DEMAND_AMAP_PAGES, 2))
    offset = max(5, min(ON_DEMAND_AMAP_OFFSET, 25))
    try:
        key = _read_key()
        completed = _completed_runs(city, keywords, pages, offset, force=(before == 0))
        rows: list[dict] = []
        seen: set[str] = set()
        keyword_counts: dict[str, int] = {}
        skipped: list[str] = []
        errors: list[str] = []
        for keyword in keywords:
            if keyword in completed:
                skipped.append(keyword)
                continue
            start_count = len(rows)
            try:
                for page in range(1, pages + 1):
                    for poi in _fetch_page(key, city, keyword, page, offset):
                        row = _normalize_poi(poi, city, keyword)
                        if not row or row["source_id"] in seen:
                            continue
                        seen.add(row["source_id"])
                        rows.append(row)
                    time_sleep = float(os.getenv("ON_DEMAND_AMAP_SLEEP", "0.08"))
                    if time_sleep > 0:
                        import time
                        time.sleep(min(time_sleep, 0.5))
                keyword_counts[keyword] = len(rows) - start_count
            except Exception as exc:
                error_text = str(exc)
                errors.append(f"{keyword}: {error_text}")
                _record_import_run(city, keyword, pages, offset, len(rows) - start_count, "failed", error_text)
                logger.warning("高德按需补库关键词失败 city=%s keyword=%s: %s", city, keyword, exc)
                if "USERKEY_PLAT_NOMATCH" in error_text or "INVALID_USER_KEY" in error_text:
                    break

        if not rows and errors:
            return {
                "status": "failed",
                "city": city,
                "before": before,
                "inserted": 0,
                "updated": 0,
                "error": "；".join(errors[:3]),
            }
        inserted, updated = _upsert_places(rows, replace_city=False, city=city)
        for keyword, count in keyword_counts.items():
            _record_import_run(city, keyword, pages, offset, count, "success")
        after = _city_place_count(city)
        return {
            "status": "ok",
            "city": city,
            "before": before,
            "after": after,
            "inserted": inserted,
            "updated": updated,
            "keywords": keywords,
            "skipped": skipped,
        }
    except BaseException as exc:
        logger.warning("高德按需补库失败 city=%s: %s", city, exc)
        return {"status": "failed", "city": city, "before": before, "error": str(exc)}


async def _load_agent_context(current_user: dict) -> tuple[list, list, list, list, dict]:
    """
    只有确认为 plan 后才查询 places / family_members。
    避免普通聊天也查库、进 Agent，降低延迟。
    """
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            city = _normalize_city(current_user.get("city") or "北京")
            has_city = _places_has_city_column(cursor)
            city_select = "p.city," if has_city else "%s AS city,"
            city_where = "WHERE p.city = %s" if has_city else ""
            sql_args = (city,) if not has_city else (city,)
            cursor.execute(f"""
                SELECT p.name, p.category, p.keyword, p.address, p.open_hours, p.price_range, p.score,
                       p.desc_text, p.lng, p.lat, p.source, p.source_id, p.phone, {city_select}
                       cs.available AS available_seats,
                       COALESCE(cs.queue_count, 0) AS queue_count,
                       COALESCE(cs.status, 'available') AS capacity_status
                FROM places p
                LEFT JOIN place_capacity_status cs ON cs.place_id = p.id
                {city_where}
            """, sql_args)
            places = cursor.fetchall()

            cursor.execute("SELECT * FROM family_members WHERE user_id = %s", (current_user["id"],))
            family_rows = cursor.fetchall()

            family_members = []
            for r in family_rows:
                try:
                    r["tags"] = json.loads(r["tags"]) if r.get("tags") else []
                except Exception:
                    r["tags"] = []
                family_members.append(r)

            if not family_members:
                family_members = [
                    {"avatar": "👩", "relation": "老婆", "tags": ["爱拍照出片", "忌口香菜"]},
                    {"avatar": "👴", "relation": "父母", "tags": ["腿脚不能久站", "口味清淡"]},
                ]

            preference_memory = _load_preference_memory_for_user(current_user)
            session_history = _get_session_history(current_user)
            weather_memory = {
                "city": str(current_user.get("city") or ""),
                "status": "unknown",
                "message": "尚未查询真实天气，若当前需求涉及户外或气温，请调用 get_weather。",
            }
            return places, family_members, preference_memory, session_history, weather_memory
    finally:
        conn.close()


def _fallback_intent(text: str, history: list | None = None) -> dict:
    """LLM 路由失败时的上下文感知兜底。"""
    text = (text or "").strip()

    followup_hints = [
        "换成", "换一个", "换个", "不要这个", "不要那", "再近", "近一点",
        "远一点", "室内", "户外", "清淡", "晚饭", "晚餐", "那", "这个",
        "便宜点", "预算低", "别太远", "轻松点"
    ]
    if text and _history_has_plan(history) and any(k in text for k in followup_hints):
        return {
            "intent": "plan",
            "confidence": 0.72,
            "reply": "",
            "reason": "结合最近会话，当前输入是在修改上一轮规划"
        }

    if not text:
        return {
            "intent": "chat",
            "confidence": 1.0,
            "reply": "你好，我是半日闲AI。你可以告诉我出发时间、同行人和偏好，我来帮你安排本地半日行程。",
            "reason": "空输入兜底"
        }

    if text in {"你好", "您好", "hi", "hello"} or text.lower() in {"hi", "hello"}:
        return {
            "intent": "chat",
            "confidence": 0.95,
            "reply": "你好，我是半日闲AI。我可以帮你规划本地半日出行、亲子游、约会和朋友聚会路线。",
            "reason": "问候语"
        }

    plan_hints = [
        "安排", "规划", "路线", "行程", "去哪", "去哪儿", "推荐",
        "孩子", "老婆", "朋友", "约会", "亲子", "聚餐",
        "餐厅", "景点", "附近", "周末", "今天", "下午", "晚上",
        "预约", "下单", "半日", "半天", "出去玩", "出门", "逛逛"
    ]
    strong_plan_hints = ["安排", "规划", "路线", "行程", "推荐", "孩子", "老婆", "朋友", "亲子", "餐厅", "景点", "附近", "预约", "下单"]
    weak_plan_hints = ["今天", "下午", "晚上", "周末", "出去玩", "出门", "逛逛"]

    if any(k in text for k in weak_plan_hints) and not any(k in text for k in strong_plan_hints):
        return {
            "intent": "clarify",
            "confidence": 0.62,
            "reply": "可以的。你是几个人同行？更想亲子、约会、美食还是轻松休闲？距离有什么要求吗？",
            "reason": "文本像出行需求，但缺少同行人和偏好，先追问"
        }

    if len(text) >= 8 and any(k in text for k in plan_hints):
        return {
            "intent": "plan",
            "confidence": 0.58,
            "reply": "",
            "reason": "LLM 路由失败，文本包含出行或本地生活规划要素，兜底进入规划 Agent"
        }

    return {
        "intent": "chat",
        "confidence": 0.55,
        "reply": "你好，我是半日闲AI，可以帮你规划本地半日出行、亲子游、约会和朋友聚会路线。",
        "reason": "LLM 路由失败，兜底为普通对话"
    }


async def classify_intent_by_llm(text: str, current_user: dict | None = None, history: list | None = None) -> dict:
    """用 LLM 做意图路由，并结合当前用户短期会话上下文。"""
    text = (text or "").strip()
    if not text:
        return _fallback_intent(text, history=history)

    system_prompt = (
        '你是半日闲AI的意图分类器。判断用户是否需要本地出行/路线规划。\n'
        '只输出一个 JSON，不加任何说明或代码块。\n\n'
        'intent 选项（按优先级判断）：\n\n'
        '1. plan -- 用户需要出行规划、地点推荐、路线安排\n'
        '   触发条件（满足任意一条即判 plan）：\n'
        '   - 说了时间（今天、下午、明天、周末）加 活动意图（出去、玩、逛、吃）\n'
        '   - 提到同行人（老婆、孩子、父母、朋友）并想安排活动\n'
        '   - 提到具体地点类型（餐厅、公园、咖啡、景点）\n'
        '   - 用了帮我安排/帮我规划/推荐路线/去哪玩等表达\n'
        '   - history 有 AI 规划结果，且当前是调整（换一个/近一点/室内）\n\n'
        '2. clarify -- 想规划但信息极度不足（如只说出去玩或去哪），reply 追问 1-2 个关键点\n'
        '   注意：有时间或有人物已足够直接 plan，不必 clarify\n\n'
        '3. chat -- 纯问答、产品咨询、问候，不需要地点/路线\n'
        '   例：你好、你能做什么、怎么用\n\n'
        '4. unsupported -- 完全超出产品范围\n\n'
        '输出格式示例：\n'
        '{"intent":"plan","confidence":0.9,"reply":"","reason":"..."}\n'
        '{"intent":"chat","confidence":0.95,"reply":"你好，我是半日闲AI，可以帮你规划出行路线。","reason":"..."}\n\n'
        '规则：\n'
        '- 只要文本含今天/下午/明天/周末之一加任何出行词，直接 plan\n'
        '- 帮我安排即使没细节也判 plan（AI 会追问或合理推断）\n'
        '- reply 非空仅当 intent 为 chat/clarify/unsupported\n'
        '- 不要提及 OpenAI、华为或具体模型名称，你是半日闲AI\n'
    )

    user_payload = {
        "message": text,
        "current_user": _safe_user_for_prompt(current_user),
        "session_history": history or [],
    }

    try:
        llm = create_llm_client()
        if getattr(llm, "provider_name", "") == "local":
            return _fallback_intent(text, history=history)
        raw = await asyncio.wait_for(
            llm.complete([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}
            ]),
            timeout=float(os.getenv("INTENT_TIMEOUT_SECONDS", "8"))
        )
        data = _parse_json_object(raw)
        if _is_agent_tool_decision(data):
            logger.warning(f"意图路由收到 Agent tool_call，已改走兜底分类: {raw}")
            return _fallback_intent(text, history=history)
        intent = data.get("intent")
        if intent not in {"plan", "chat", "clarify", "unsupported"}:
            logger.warning(f"意图路由返回非法 intent，已改走兜底分类: intent={intent}, raw={raw}")
            return _fallback_intent(text, history=history)
        try:
            confidence = float(data.get("confidence", 0.7))
        except Exception:
            confidence = 0.7
        return {
            "intent": intent,
            "confidence": max(0.0, min(confidence, 1.0)),
            "reply": data.get("reply") or "",
            "reason": data.get("reason") or ""
        }
    except Exception as e:
        logger.exception(f"LLM 意图路由失败: {e}")
        return _fallback_intent(text, history=history)


@router.get("/weather/current")
async def current_weather(
    city: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    current_user: dict = Depends(get_current_user),
):
    """Expose the existing weather skill for the mini-program weather card."""
    result = await get_weather(city=city, date="today", lat=lat, lng=lng, current_user=current_user)
    return {"code": 200, "data": result}


@router.post("/chat/replace-step")
async def replace_plan_step(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """替换路线中某一个地点，返回一个备选地点。

    payload:
      {
        "plan_steps": [...],     # 当前路线 steps
        "step_index": 1,         # 要替换的步骤序号（0-based）
        "category": "咖啡",      # 可选，指定类别
        "keyword": "约会",       # 可选
        "profile_text": "..."    # 可选，用户偏好文字
      }
    """
    steps = payload.get("plan_steps") or []
    step_index = int(payload.get("step_index") or 0)
    category = (payload.get("category") or "").strip()
    keyword = (payload.get("keyword") or "").strip()
    profile_text = (payload.get("profile_text") or "").strip()
    city = _resolve_request_city(profile_text, payload.get("city"), current_user)

    # 当前路线中已有地点名（排除用）
    existing_names = {
        str(s.get("name", "")).strip()
        for s in steps
        if isinstance(s, dict) and s.get("name")
    }

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            from agent.tools import search_places as _search_places, normalize_places

            # 查询地点库
            has_city = _places_has_city_column(cursor)
            city_select = "p.city," if has_city else "%s AS city,"
            city_where = "WHERE p.city = %s" if has_city else ""
            sql_args = (city,) if not has_city else (city,)
            cursor.execute(f"""
                SELECT p.name, p.category, p.keyword, p.address, p.open_hours, p.price_range,
                       p.score, p.desc_text, p.lng, p.lat, p.source, p.source_id, p.phone, {city_select}
                       COALESCE(cs.status, 'available') AS capacity_status
                FROM places p
                LEFT JOIN place_capacity_status cs ON cs.place_id = p.id
                {city_where}
            """, sql_args)
            all_places = cursor.fetchall()
    finally:
        conn.close()

    # 过滤已有地点
    candidates = [p for p in all_places if p.get("name") not in existing_names]
    if not candidates:
        raise HTTPException(status_code=404, detail="没有更多备选地点")

    # 根据 keyword/category 粗筛
    search_kw = keyword or category
    if search_kw:
        filtered = [
            p for p in candidates
            if search_kw in str(p.get("keyword", ""))
            or search_kw in str(p.get("category", ""))
            or search_kw in str(p.get("desc_text", ""))
        ]
        if filtered:
            candidates = filtered

    # 按评分+随机扰动排序（±12分），确保每次点"换一个"出现不同地点
    import random as _random
    try:
        candidates.sort(
            key=lambda p: float(p.get("score") or 0) + _random.randint(-12, 12),
            reverse=True,
        )
    except Exception:
        pass

    best = candidates[0] if candidates else None
    if not best:
        raise HTTPException(status_code=404, detail="没有更多备选地点")

    desc_text = (best.get("desc_text") or "").strip()
    price_range = (best.get("price_range") or "").strip()
    meta_parts = [p for p in [desc_text, price_range] if p]
    meta = "；".join(meta_parts) or "备选推荐"

    # 构建 step 格式
    original_step = steps[step_index] if 0 <= step_index < len(steps) else {}
    new_step = {
        "index": step_index + 1,
        "time":  original_step.get("time", ""),
        "name":  best.get("name", ""),
        "category": best.get("category", ""),
        "keyword": best.get("keyword", ""),
        "meta":  meta,
        "reason": f"评分{best.get('score', '')}，适合当前路线风格",
        "lng":   float(best.get("lng") or 0),
        "lat":   float(best.get("lat") or 0),
        "address": best.get("address", ""),
        "open_hours": best.get("open_hours", ""),
        "price_range": best.get("price_range", ""),
        "source": best.get("source", ""),
        "source_id": best.get("source_id", ""),
        "phone": best.get("phone", ""),
    }

    logger.info(f"🔄 替换地点: 用户={current_user['name']} step={step_index} → {new_step['name']}")
    return {"code": 200, "data": {"step": new_step}}


@router.post("/chat/send")
async def ai_chat_agent(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    text = (payload.get("text") or "").strip()
    route_style = (
        payload.get("style")
        or payload.get("routeStyle")
        or payload.get("planStyle")
        or payload.get("mode")
        or payload.get("preferenceStyle")
        or ""
    )
    route_style = str(route_style).strip()

    # 提取前端上报的定位数据（腾讯逆地理编码结果）
    def _to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    user_lat = _to_float(payload.get("lat") or payload.get("latitude"))
    user_lng = _to_float(payload.get("lng") or payload.get("longitude"))
    user_district = str(payload.get("district") or "").strip() or None
    selected_city = _resolve_request_city(text, payload.get("city"), current_user)

    # 将定位数据注入 current_user 副本，不污染认证对象
    enriched_user: dict = {**current_user, "city": selected_city}
    if user_lat is not None:
        enriched_user["lat"] = user_lat
    if user_lng is not None:
        enriched_user["lng"] = user_lng
    if user_district:
        enriched_user["district"] = user_district

    logger.info(f"💬 [{current_user['name']}] => {text}" + (
        f" [城市: {selected_city}]" + (f" [定位: {user_lat},{user_lng} {user_district or ''}]" if user_lat else "")
    ))
    history = _get_session_history(current_user)

    async def stream_generator():
        final_data = None
        try:
            # 1. 先返回状态，避免前端长时间无响应
            yield sse_format(status_event("已收到消息，正在判断需求类型"))

            if _is_weather_request(text):
                city = _resolve_weather_city(text, enriched_user)
                yield sse_format(intent_event(
                    intent="chat",
                    confidence=0.98,
                    reason="用户在询问实时天气，直接调用天气工具，不进入普通聊天兜底"
                ))
                yield sse_format(status_event(f"正在查询{city}实时天气"))
                arguments = {"city": city, "date": "today"}
                if user_lat is not None and user_lng is not None:
                    arguments["lat"] = user_lat
                    arguments["lng"] = user_lng
                yield sse_format(skill_call_event(
                    skill="get_weather",
                    arguments=arguments,
                    visible_reason="用户询问天气，需要调用真实天气工具，不能编造天气"
                ))
                weather_result = await get_weather(
                    city=city, date="today",
                    lat=user_lat, lng=user_lng,
                    current_user=enriched_user,
                )
                observation = {
                    "ok": True,
                    "tool_name": "get_weather",
                    "skill_name": "get_weather",
                    "result": weather_result,
                }
                yield sse_format(observation_event(
                    skill="get_weather",
                    summary=_weather_summary(weather_result),
                    data=observation,
                ))
                final_data = _build_weather_done_data(weather_result, city)
                _append_session_history(current_user, text, _summarize_ai_output(final_data))
                _save_preference_memories(current_user, text, final_data)
                yield sse_format(done_event(final_data))
                return

            # 2. 明确规划/明确不规划先走规则快判，避免每次都等远程 LLM 意图路由。
            _text_has_plan_signal = _has_strong_plan_signal(text)
            _text_has_no_plan_signal = _has_no_plan_signal(text)

            if _text_has_no_plan_signal:
                intent = "chat"
                intent_result = {
                    "intent": "chat",
                    "confidence": 0.9,
                    "reply": "",
                    "reason": "用户明确要求不要规划，保持普通聊天",
                }
            elif _text_has_plan_signal:
                intent = "plan"
                intent_result = {
                    "intent": "plan",
                    "confidence": 0.9,
                    "reply": "",
                    "reason": "文本含明确出行规划信号，跳过远程意图路由",
                }
            else:
                intent_result = await classify_intent_by_llm(text, current_user=enriched_user, history=history)
                intent = intent_result.get("intent", "chat")

            yield sse_format(intent_event(
                intent=intent,
                confidence=intent_result.get("confidence"),
                reason=intent_result.get("reason"),
            ))

            # 3. 不是路线/本地生活规划，不进入 Agent，直接调用 LLM 回复
            if intent in {"chat", "unsupported"}:
                reply = intent_result.get("reply")
                # 如果 Router 本身失败后走了兜底，仍然再尝试一次普通 LLM 回复；
                # 只有普通回复也失败时，answer_chat_by_llm 才会返回固定兜底话术。
                if (not reply) or ("LLM 路由失败" in str(intent_result.get("reason", ""))):
                    reply = await answer_chat_by_llm(text, current_user=enriched_user, history=history)

                final_data = {"type": "text", "content": reply}
                _append_session_history(current_user, text, _summarize_ai_output(final_data))
                _save_preference_memories(current_user, text, final_data)
                yield sse_format(done_event(final_data))
                return

            # 4. 信息不足时，不硬规划，先追问
            if intent == "clarify":
                reply = intent_result.get("reply") or "可以的。你想什么时候出发？几个人同行？更偏亲子、约会、美食还是休闲？"
                final_data = {"type": "text", "intent": "clarify", "content": reply}
                _append_session_history(current_user, text, _summarize_ai_output(final_data))
                _save_preference_memories(current_user, text, final_data)
                yield sse_format(done_event(final_data))
                return

            # 5. 只有 plan 才加载地点库和进入 Agent
            yield sse_format(status_event(f"识别为出行规划需求，正在读取{selected_city}地点库"))
            if selected_city != "北京":
                current_count = _city_place_count(selected_city)
                if current_count < ON_DEMAND_AMAP_MIN_PLACES:
                    # 后台异步补充，不阻塞当前请求
                    asyncio.create_task(
                        asyncio.to_thread(_ensure_city_places_from_amap, selected_city, text)
                    )
                    if current_count == 0:
                        yield sse_format(status_event(
                            f"正在后台初始化{selected_city}地点库，本次使用通用地点规划"
                        ))

            places, family_members, preference_memory, session_history, weather_memory = await _load_agent_context(enriched_user)

            yield sse_format(status_event("正在调用规划 Agent"))
            agent_text = text
            if route_style and route_style not in agent_text:
                agent_text = f"{text}\n路线风格：{route_style}"

            async for event in run_agent_stream(
                text=agent_text,
                current_user=enriched_user,
                family_members=family_members,
                places=places,
                history=history,
                preference_memory=preference_memory,
                session_history=session_history,
                weather_memory=weather_memory,
            ):
                if event.get("type") == "done":
                    final_data = event.get("data")
                    _append_session_history(current_user, text, _summarize_ai_output(final_data))
                    _save_preference_memories(current_user, text, final_data)
                yield sse_format(event)

        except Exception as e:
            logger.exception(f"Chat 接口处理失败: {e}")
            yield sse_format(error_event("AI 引擎暂时不可用，请稍后重试"))

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )
