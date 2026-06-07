# -*- coding: utf-8 -*-
"""Persistent agent memory storage helpers."""

from __future__ import annotations

import json
import logging
import re

from core.database import get_db


logger = logging.getLogger("BanrixianAPI")


# ---------------------------------------------------------------------------
# 亲友关系映射表
# (canonical_relation, avatar, aliases)
# ---------------------------------------------------------------------------
_RELATION_DEFS: list[tuple[str, str, list[str]]] = [
    ("老婆",   "👩", ["老婆", "妻子", "爱人", "媳妇"]),
    ("老公",   "👨", ["老公", "丈夫", "先生"]),
    ("女朋友", "👩", ["女朋友", "女友"]),
    ("男朋友", "👨", ["男朋友", "男友"]),
    ("儿子",   "👦", ["儿子", "小子"]),
    ("女儿",   "👧", ["女儿", "闺女", "小公主"]),
    ("孩子",   "🧒", ["孩子", "小孩", "宝宝", "娃", "宝贝"]),
    ("爸爸",   "👴", ["爸爸", "老爸", "父亲"]),
    ("妈妈",   "👵", ["妈妈", "老妈", "母亲"]),
    ("父母",   "👴", ["父母", "爸妈", "老人"]),
    ("爷爷",   "👴", ["爷爷"]),
    ("奶奶",   "👵", ["奶奶", "姥姥", "外婆"]),
    ("朋友",   "👥", ["朋友", "好友", "闺蜜", "好哥们", "好兄弟"]),
]

# 别名 → (canonical, avatar) 反查表
_ALIAS_MAP: dict[str, tuple[str, str]] = {}
for _rel, _av, _aliases in _RELATION_DEFS:
    for _alias in _aliases:
        _ALIAS_MAP[_alias] = (_rel, _av)

_SPECIFIC_RELATIONS = {"老婆", "老公", "女朋友", "男朋友", "儿子", "女儿", "爸爸", "妈妈", "爷爷", "奶奶", "朋友"}


# ---------------------------------------------------------------------------
# 特征提取规则：(tag文本, 触发关键词列表)
# 每条规则只要关键词出现在整句话里即可，不强制紧邻关系词
# ---------------------------------------------------------------------------
_TAG_RULES: list[tuple[str, list[str]]] = [
    # 饮食
    ("减肥中",        ["减肥", "瘦身", "控体重"]),
    ("饮食清淡",      ["清淡", "少油", "低脂", "低卡", "健康饮食"]),
    ("忌口香菜",      ["香菜", "不吃香菜", "忌口香菜"]),
    ("不吃辣",        ["不吃辣", "忌辣", "不能辣", "怕辣"]),
    ("素食",          ["素食", "吃素", "不吃肉", "纯素"]),
    ("乳糖不耐",      ["乳糖不耐", "不喝牛奶", "牛奶过敏"]),
    ("有食物过敏",    ["过敏", "食物过敏", "海鲜过敏", "花生过敏"]),
    ("控糖",          ["控糖", "糖尿病", "血糖高"]),
    ("忌口",          ["忌口"]),
    # 健康/行动
    ("腿脚不便",      ["腿脚不好", "腿脚不便", "行动不便", "不能久站", "不能久走", "腿脚"]),
    ("高血压",        ["高血压"]),
    ("心脏病",        ["心脏病", "心脏不好"]),
    ("孕妇",          ["怀孕", "孕妇", "孕期", "待产"]),
    ("晕车",          ["晕车", "晕船"]),
    # 年龄/学段
    ("婴幼儿",        ["婴儿", "刚出生", "几个月大", "半岁"]),
    ("上幼儿园",      ["幼儿园", "幼儿"]),
    ("上小学",        ["上小学", "小学生", "小学"]),
    ("上初中",        ["上初中", "初中生", "初中"]),
    ("上高中",        ["上高中", "高中生", "高中"]),
    ("上大学",        ["上大学", "大学生", "大学", "大一", "大二", "大三", "大四"]),
    # 兴趣爱好
    ("爱拍照出片",    ["爱拍照", "喜欢拍照", "拍照", "出片", "打卡"]),
    ("爱逛街",        ["爱逛街", "喜欢逛街", "逛街"]),
    ("爱美食",        ["爱美食", "喜欢吃", "吃货"]),
    ("爱运动",        ["爱运动", "喜欢运动", "健身", "跑步"]),
    ("爱艺术文化",    ["博物馆", "展览", "艺术", "文化", "历史"]),
    ("爱自然户外",    ["公园", "爬山", "户外", "自然", "徒步"]),
    ("爱看书/阅读",   ["爱看书", "爱读书", "看书", "读书", "阅读", "喜欢看书", "喜欢读书"]),
    ("爱画画",        ["爱画画", "喜欢画画", "画画", "绘画"]),
    ("爱音乐",        ["爱音乐", "喜欢音乐", "唱歌", "弹琴", "钢琴", "吉他", "唱歌"]),
    ("爱游泳",        ["爱游泳", "喜欢游泳", "游泳"]),
    ("爱跳舞",        ["爱跳舞", "喜欢跳舞", "跳舞"]),
    ("爱踢球",        ["爱踢球", "踢足球", "踢球", "足球"]),
    ("爱打球",        ["爱打球", "打篮球", "打乒乓", "羽毛球"]),
    ("爱看动画",      ["看动画", "看动漫", "动画片", "动漫", "卡通"]),
    ("爱玩游戏",      ["打游戏", "电子游戏", "手机游戏", "玩游戏"]),
    ("爱骑车",        ["骑自行车", "骑车", "骑单车"]),
    # 性格/偏好
    ("怕热",          ["怕热", "热了难受"]),
    ("怕冷",          ["怕冷", "怕冻"]),
    ("内向安静",      ["内向", "安静", "不爱热闹"]),
    ("喜欢安静环境",  ["喜欢安静", "不喜欢吵", "安静的地方"]),
]

# 单字兴趣词（如"书"、"画"），与多字兴趣词同等有效
_SINGLE_CHAR_INTERESTS = {"书", "画", "舞", "球", "棋", "诗", "歌"}


def _cn_num(s: str) -> int | None:
    cn = {"零":0,"一":1,"二":2,"两":2,"俩":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
    return cn.get(s)


def _extract_age_tag(text: str) -> str | None:
    """从文本提取年龄 tag，如 '5岁' / '8岁'。"""
    m = re.search(r"([0-9一二两俩三四五六七八九十]+)\s*岁", text)
    if m:
        raw = m.group(1)
        age = int(raw) if raw.isdigit() else _cn_num(raw)
        if age is not None:
            return f"{age}岁"
    return None


def _find_relations_in_text(text: str) -> list[tuple[str, str, int]]:
    """返回文本中所有关系词出现位置，(canonical, avatar, pos)，按位置排序。"""
    found: list[tuple[str, str, int]] = []
    # 按别名长度降序匹配，防止"老婆"被"婆"截断
    for alias in sorted(_ALIAS_MAP.keys(), key=len, reverse=True):
        for m in re.finditer(re.escape(alias), text):
            canonical, avatar = _ALIAS_MAP[alias]
            found.append((canonical, avatar, m.start()))
    # 动态补充：处理"新的儿子/二胎女儿/小女儿/大儿子/我闺女"等自然说法。
    dynamic_patterns = [
        (r"(?:我|我的|家里|家中|有个|有一个|新增|新的|新添|二胎|老大|老二|大|小|二|第二)?\s*(儿子|男孩|男娃)", "儿子", "👦"),
        (r"(?:我|我的|家里|家中|有个|有一个|新增|新的|新添|二胎|老大|老二|大|小|二|第二)?\s*(女儿|闺女|女孩|女娃)", "女儿", "👧"),
    ]
    for pattern, canonical, avatar in dynamic_patterns:
        for m in re.finditer(pattern, text):
            found.append((canonical, avatar, m.start()))
    # 去重：同 canonical 保留最早出现的
    seen: dict[str, tuple[str, str, int]] = {}
    for canonical, avatar, pos in sorted(found, key=lambda x: x[2]):
        if canonical not in seen:
            seen[canonical] = (canonical, avatar, pos)
    return list(seen.values())


def _split_clauses(text: str) -> list[tuple[int, int, str]]:
    """按中文分隔符切分句子，返回 [(start, end, clause), ...]。"""
    pattern = r'[，。；,\.！？!?]'
    clauses: list[tuple[int, int, str]] = []
    last = 0
    for m in re.finditer(pattern, text):
        clauses.append((last, m.start(), text[last:m.start()]))
        last = m.end()
    if last < len(text):
        clauses.append((last, len(text), text[last:]))
    return clauses


def _extract_tags_for_relation(text: str, relation: str, pos: int) -> list[str]:
    """
    提取与某关系词相关的特征 tag。
    策略：找关系词所在的句子分句，只在该分句 + 紧邻分句中提取特征。
    """
    tags: list[str] = []
    clauses = _split_clauses(text)

    # 找关系词属于哪个分句
    rel_clause_idx = 0
    for i, (start, end, _) in enumerate(clauses):
        if start <= pos < end:
            rel_clause_idx = i
            break

    # 取当前分句 + 前后各一分句作为上下文窗口
    window_clauses = clauses[max(0, rel_clause_idx - 1): rel_clause_idx + 2]
    window = "".join(c for _, _, c in window_clauses)

    # 年龄 tag（仅孩子类关系）
    if relation in ("儿子", "女儿", "孩子"):
        age_tag = _extract_age_tag(window)
        if age_tag:
            tags.append(age_tag)

    # 通用特征 tag：仅在窗口内匹配
    for tag, keywords in _TAG_RULES:
        if any(kw in window for kw in keywords):
            tags.append(tag)

    # ── 自由兴趣提取：捕获"喜欢X/爱X/热爱X/迷上X"等未被规则覆盖的兴趣 ──
    _INTEREST_PREFIXES = ["喜欢", "爱好", "热爱", "喜爱", "迷上", "着迷", "爱看", "爱玩", "爱吃"]
    _SKIP_INTERESTS = {
        "逛街", "拍照", "吃", "玩", "美食", "运动", "安静", "室内", "户外", "",
        "出行", "旅游", "购物", "工作", "学习", "睡觉", "休息",
    }
    for prefix in _INTEREST_PREFIXES:
        m = re.search(re.escape(prefix) + r'([^\s，。；！？、的了呢吗啊]{1,8})', window)
        if m:
            interest = m.group(1).strip().rstrip("的了呢吗啊")
            # 过滤已被 _TAG_RULES 覆盖的兴趣，避免重复
            already_covered = any(interest in kw or kw in interest for _, kws in _TAG_RULES for kw in kws)
            if interest and interest not in _SKIP_INTERESTS \
                    and (len(interest) >= 2 or interest in _SINGLE_CHAR_INTERESTS) \
                    and not already_covered:
                tag = f"喜欢{interest}" if not interest.startswith("喜欢") else interest
                tags.append(tag)
                break  # 每个分句只取一个自由兴趣，避免噪音

    return list(dict.fromkeys(tags))  # 去重保序


def _extract_family_members_from_text(text: str) -> list[dict]:
    """从对话文本提取新亲友及其特征，返回 [{relation, avatar, tags}]。"""
    text = text or ""
    relations = _find_relations_in_text(text)
    results: list[dict] = []
    for canonical, avatar, pos in relations:
        tags = _extract_tags_for_relation(text, canonical, pos)
        if tags or canonical:  # 哪怕没有 tag 也记录关系本身
            results.append({"relation": canonical, "avatar": avatar, "tags": tags})
    return results


# 泛化关系 → 优先合并到的具体关系列表（按优先级排序）
_GENERIC_TO_SPECIFIC: dict[str, list[str]] = {
    "孩子":  ["儿子", "女儿"],
    "父母":  ["爸爸", "妈妈"],
    "爱人":  ["老婆", "老公", "女朋友", "男朋友"],
}


def _resolve_target_relation(
    cursor,
    user_id: str,
    relation: str,
) -> str | None:
    """
    如果 relation 是泛化词（孩子/父母/爱人），检查用户已有具体成员，
    返回应该合并到的具体 relation 名；无法合并时返回 None（保持原 relation）。
    """
    specifics = _GENERIC_TO_SPECIFIC.get(relation)
    if not specifics:
        return None  # 不是泛化词，无需处理
    # 查询用户已有的具体关系
    placeholders = ",".join(["%s"] * len(specifics))
    cursor.execute(
        f"SELECT relation FROM family_members WHERE user_id = %s AND relation IN ({placeholders})",
        (user_id, *specifics),
    )
    rows = cursor.fetchall()
    if rows:
        # 返回已有的第一个具体关系（按 specifics 优先级）
        existing = {r["relation"] for r in rows}
        for s in specifics:
            if s in existing:
                return s
    return None  # 没有具体关系，可以新建泛化条目


def _auto_upsert_family_members(current_user: dict, text: str) -> None:
    """从对话文本中自动识别亲友，将新成员或新 tag 写入 family_members 表。
    泛化关系词（孩子/父母）会优先合并到已有的具体成员（儿子/女儿/爸爸/妈妈）。
    """
    candidates = _extract_family_members_from_text(text)
    if not candidates:
        return

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            for member in candidates:
                relation  = member["relation"]
                avatar    = member["avatar"]
                new_tags: list[str] = member["tags"]

                # 泛化词优先解析为已有具体成员
                resolved = _resolve_target_relation(cursor, current_user["id"], relation)
                target_relation = resolved if resolved else relation

                cursor.execute(
                    "SELECT id, tags FROM family_members WHERE user_id = %s AND relation = %s",
                    (current_user["id"], target_relation),
                )
                row = cursor.fetchone()

                if row:
                    # 合并 tags
                    try:
                        existing_tags = json.loads(row["tags"]) if row.get("tags") else []
                        if not isinstance(existing_tags, list):
                            existing_tags = []
                    except Exception:
                        existing_tags = []
                    merged = list(dict.fromkeys(existing_tags + new_tags))[:20]
                    if merged != existing_tags:
                        cursor.execute(
                            "UPDATE family_members SET tags = %s WHERE id = %s",
                            (json.dumps(merged, ensure_ascii=False), row["id"]),
                        )
                        action = f"合并到[{target_relation}]" if resolved else "更新"
                        logger.info(f"👨‍👩‍👧 {action}: {current_user['name']} -> {target_relation} tags={merged}")
                elif new_tags or target_relation in _SPECIFIC_RELATIONS:
                    # 具体关系即使暂无标签也建档；泛化词无标签时仍不建，避免空"孩子/父母"噪音。
                    cursor.execute(
                        "INSERT INTO family_members (user_id, avatar, relation, tags) VALUES (%s, %s, %s, %s)",
                        (current_user["id"], avatar, target_relation,
                         json.dumps(new_tags, ensure_ascii=False)),
                    )
                    logger.info(f"👨‍👩‍👧 自动新建亲友: {current_user['name']} -> {target_relation} tags={new_tags}")
                # 注意：泛化词且无 tag 时不新建（避免产生空 "孩子" 记录）

        conn.commit()
    except Exception as exc:
        logger.warning(f"自动写入亲友失败: {exc}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 以下为原有偏好记忆函数，保持不变
# ---------------------------------------------------------------------------

def _load_family_memory_for_user(current_user: dict) -> list:
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT avatar, relation, tags FROM family_members WHERE user_id = %s", (current_user["id"],))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                try:
                    row["tags"] = json.loads(row["tags"]) if row.get("tags") else []
                except Exception:
                    row["tags"] = []
                result.append(row)
            return result
    finally:
        conn.close()


def _load_preference_memory_for_user(current_user: dict) -> list:
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, memory_type, title, content, relation, source, weight, created_at, updated_at
                FROM agent_memories
                WHERE user_id = %s
                ORDER BY updated_at DESC, id DESC
                LIMIT 30
                """,
                (current_user["id"],),
            )
            rows = cursor.fetchall()
            for row in rows:
                for key in ("created_at", "updated_at"):
                    if row.get(key) is not None:
                        row[key] = str(row[key])
            return rows
    finally:
        conn.close()


def _memory_key(title: str) -> str:
    return title.strip()[:128]


def _upsert_agent_memory(
    current_user: dict,
    memory_type: str,
    title: str,
    content: str,
    relation: str = "",
    source: str = "chat",
) -> None:
    title = _memory_key(title)
    content = content.strip()
    relation = (relation or "").strip()
    if not title or not content:
        return

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_memories
                    (user_id, memory_type, title, content, relation, source, weight, created_at, updated_at)
                VALUES
                    (%s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    source = VALUES(source),
                    weight = LEAST(weight + 1, 99),
                    updated_at = NOW()
                """,
                (current_user["id"], memory_type, title, content, relation, source),
            )
        conn.commit()
    finally:
        conn.close()


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _extract_memory_candidates(user_text: str, final_data: dict | None) -> list[dict]:
    text = user_text or ""
    candidates: list[dict] = []

    def add(memory_type: str, title: str, content: str, relation: str = "") -> None:
        item = {"memory_type": memory_type, "title": title, "content": content, "relation": relation}
        if item not in candidates:
            candidates.append(item)

    if _contains_any(text, ["清淡", "减肥", "低脂", "低卡", "低油", "少油", "健康一点"]):
        add("preference", "饮食低负担", "用户偏好清淡、低油或低负担饮食")
    if _contains_any(text, ["忌口", "不吃", "过敏", "不要辣", "不吃辣", "香菜"]):
        add("preference", "饮食忌口需确认", "用户提到忌口或过敏，规划餐饮前需要确认")
    if _contains_any(text, ["孩子", "小孩", "亲子", "儿童", "儿子", "女儿"]):
        add("preference", "亲子友好", "用户有亲子出行场景，优先考虑安全、低强度、适合孩子的地点")
    if _contains_any(text, ["别太远", "不要太远", "近一点", "附近", "再近"]):
        add("preference", "距离不要太远", "用户偏好距离较近、动线不要太累的路线")
    if _contains_any(text, ["室内", "换成室内"]):
        add("preference", "偏好室内备选", "用户在天气或体验不确定时偏好室内地点")

    family_patterns = [
        ("老婆", ["减肥", "清淡", "低脂", "低卡"], "老婆饮食低负担", "老婆偏好或近期需要低负担饮食"),
        ("妻子", ["减肥", "清淡", "低脂", "低卡"], "妻子饮食低负担", "妻子偏好或近期需要低负担饮食"),
        ("孩子", ["5岁", "五岁", "小", "儿童", "亲子"], "孩子需要亲子友好", "孩子同行时优先选择亲子友好、安全、低强度地点"),
        ("父母", ["腿脚", "少走", "清淡", "老人"], "父母出行照顾", "父母同行时注意少走路、口味清淡和休息便利"),
    ]
    for relation, hints, title, content in family_patterns:
        if relation in text and _contains_any(text, hints):
            add("family_preference", title, content, relation=relation)

    if isinstance(final_data, dict) and final_data.get("type") == "plan":
        profile = final_data.get("profile") if isinstance(final_data.get("profile"), dict) else {}
        for preference in profile.get("preferences") or []:
            if preference in {"亲子友好", "适合多人同行", "低油低负担", "可选择清淡菜品"}:
                add("preference", str(preference), f"用户规划偏好：{preference}")
        for constraint in profile.get("constraints") or []:
            if any(key in str(constraint) for key in ["距离", "饮食", "下午", "室内"]):
                add("preference", str(constraint), f"用户规划约束：{constraint}")

    return candidates[:12]


def _save_preference_memories(current_user: dict, user_text: str, final_data: dict | None) -> None:
    try:
        for item in _extract_memory_candidates(user_text, final_data):
            _upsert_agent_memory(
                current_user=current_user,
                memory_type=item["memory_type"],
                title=item["title"],
                content=item["content"],
                relation=item.get("relation", ""),
                source="chat",
            )
    except Exception as exc:
        logger.warning(f"AI 偏好记忆写入失败: {exc}")

    # 自动提取并写入亲友成员
    try:
        _auto_upsert_family_members(current_user, user_text)
    except Exception as exc:
        logger.warning(f"自动亲友写入失败: {exc}")
