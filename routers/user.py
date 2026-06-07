# -*- coding: utf-8 -*-
"""User profile routes."""

from __future__ import annotations

import json
import logging
from fastapi import APIRouter, HTTPException, Body, Depends

import pymysql

from core.auth import get_current_user
from core.database import get_db
from core.errors import DatabaseError


logger = logging.getLogger("BanrixianAPI")

router = APIRouter(prefix="/v1/user", tags=["user"])


@router.get("/profile")
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    """Get current user profile."""
    current_user["tags"] = json.loads(current_user["tags"]) if current_user.get("tags") else []
    if "password" in current_user:
        del current_user["password"]
    return {"code": 200, "message": "success", "data": current_user}


@router.put("/profile")
async def update_profile(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """Update user profile."""
    conn = get_db()
    try:
        name = payload.get("name", current_user["name"])
        city = payload.get("city", current_user["city"])
        phone = payload.get("phone", current_user["phone"])
        tags = payload.get("tags", json.loads(current_user["tags"]) if current_user.get("tags") else [])
        
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET name = %s, city = %s, phone = %s, tags = %s WHERE id = %s", 
                (name, city, phone, json.dumps(tags), current_user["id"])
            )
        conn.commit()
        logger.info(f"📝 用户资料已更新: {name} | {city} | {phone}")
        return {"code": 200, "message": "success"}
    except pymysql.Error as e:
        logger.error(f"更新用户资料时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="更新失败")
    finally:
        conn.close()


@router.post("/sign")
async def user_daily_sign(current_user: dict = Depends(get_current_user)):
    """Daily sign-in for user."""
    conn = get_db()
    try:
        new_pts = current_user["pts"] + 50
        with conn.cursor() as cursor:
            cursor.execute("UPDATE users SET pts = %s WHERE id = %s", (new_pts, current_user["id"]))
        conn.commit()
        logger.info(f"📅 用户签到成功: {current_user['name']} (+50积分, 当前: {new_pts})")
        return {
            "code": 200,
            "message": "success",
            "data": {"msg": "签到成功", "bonusPoints": 50, "currentPoints": new_pts}
        }
    except pymysql.Error as e:
        logger.error(f"用户签到时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="签到失败")
    finally:
        conn.close()


def _parse_tags(raw) -> list:
    if isinstance(raw, list):
        return raw
    try:
        result = json.loads(raw) if raw else []
        return result if isinstance(result, list) else []
    except Exception:
        return []


@router.get("/family")
async def get_family_members(current_user: dict = Depends(get_current_user)):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM family_members WHERE user_id = %s ORDER BY id ASC", (current_user["id"],))
            rows = cursor.fetchall()
            data = [{**r, "tags": _parse_tags(r.get("tags"))} for r in rows]
            return {"code": 200, "data": data}
    except pymysql.Error as e:
        logger.error(f"查询亲友失败: {e}")
        raise HTTPException(status_code=500, detail="查询失败")
    finally:
        conn.close()


@router.post("/family")
async def add_family_member(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    relation = (payload.get("relation") or "").strip()
    if not relation:
        raise HTTPException(status_code=400, detail="relation 不能为空")
    avatar = (payload.get("avatar") or "👤").strip()
    tags = _parse_tags(payload.get("tags"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # 同一用户同一 relation 只保留一条
            cursor.execute(
                "SELECT id FROM family_members WHERE user_id = %s AND relation = %s",
                (current_user["id"], relation),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "UPDATE family_members SET avatar = %s, tags = %s WHERE id = %s",
                    (avatar, json.dumps(tags, ensure_ascii=False), existing["id"]),
                )
                record_id = existing["id"]
            else:
                cursor.execute(
                    "INSERT INTO family_members (user_id, avatar, relation, tags) VALUES (%s, %s, %s, %s)",
                    (current_user["id"], avatar, relation, json.dumps(tags, ensure_ascii=False)),
                )
                record_id = cursor.lastrowid
        conn.commit()
        logger.info(f"👨‍👩‍👧 新增/更新亲友: {current_user['name']} -> {relation}")
        return {"code": 200, "message": "success", "data": {"id": record_id, "relation": relation, "avatar": avatar, "tags": tags}}
    except pymysql.Error as e:
        logger.error(f"新增亲友失败: {e}")
        raise HTTPException(status_code=500, detail="新增失败")
    finally:
        conn.close()


@router.put("/family/{member_id}")
async def update_family_member(member_id: int, payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM family_members WHERE id = %s AND user_id = %s",
                (member_id, current_user["id"]),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="成员不存在")

            relation = (payload.get("relation") or row["relation"]).strip()
            avatar = (payload.get("avatar") or row["avatar"] or "👤").strip()
            # tags 支持全量替换或追加
            if "tags" in payload:
                tags = _parse_tags(payload["tags"])
            else:
                tags = _parse_tags(row.get("tags"))

            cursor.execute(
                "UPDATE family_members SET relation = %s, avatar = %s, tags = %s WHERE id = %s",
                (relation, avatar, json.dumps(tags, ensure_ascii=False), member_id),
            )
        conn.commit()
        return {"code": 200, "message": "success", "data": {"id": member_id, "relation": relation, "avatar": avatar, "tags": tags}}
    except HTTPException:
        raise
    except pymysql.Error as e:
        logger.error(f"更新亲友失败: {e}")
        raise HTTPException(status_code=500, detail="更新失败")
    finally:
        conn.close()


@router.delete("/family/{member_id}")
async def delete_family_member(member_id: int, current_user: dict = Depends(get_current_user)):
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM family_members WHERE id = %s AND user_id = %s",
                (member_id, current_user["id"]),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="成员不存在")
        conn.commit()
        return {"code": 200, "message": "已删除"}
    except HTTPException:
        raise
    except pymysql.Error as e:
        logger.error(f"删除亲友失败: {e}")
        raise HTTPException(status_code=500, detail="删除失败")
    finally:
        conn.close()


@router.patch("/family/{member_id}/tags")
async def merge_family_member_tags(member_id: int, payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """追加 tags，不覆盖已有标签。"""
    new_tags = _parse_tags(payload.get("tags"))
    if not new_tags:
        raise HTTPException(status_code=400, detail="tags 不能为空")
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM family_members WHERE id = %s AND user_id = %s",
                (member_id, current_user["id"]),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="成员不存在")
            existing_tags = _parse_tags(row.get("tags"))
            merged = list(dict.fromkeys(existing_tags + new_tags))[:20]
            cursor.execute(
                "UPDATE family_members SET tags = %s WHERE id = %s",
                (json.dumps(merged, ensure_ascii=False), member_id),
            )
        conn.commit()
        return {"code": 200, "message": "success", "data": {"id": member_id, "tags": merged}}
    except HTTPException:
        raise
    except pymysql.Error as e:
        logger.error(f"更新亲友标签失败: {e}")
        raise HTTPException(status_code=500, detail="更新失败")
    finally:
        conn.close()
