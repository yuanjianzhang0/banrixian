# -*- coding: utf-8 -*-
"""行程分享路由 — 生成短 ID 分享链接，无需登录即可查看。"""

from __future__ import annotations

import json
import logging
import random
import string
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from core.auth import get_current_user
from core.database import get_db

logger = logging.getLogger("BanrixianAPI")
router = APIRouter(prefix="/v1/share", tags=["share"])

_SHARE_ID_CHARS = string.ascii_lowercase + string.digits
_SHARE_EXPIRE_DAYS = 30


def _gen_share_id(length: int = 8) -> str:
    return "".join(random.choices(_SHARE_ID_CHARS, k=length))


def _base_url(request: Request) -> str:
    origin = request.headers.get("origin") or ""
    if origin:
        return origin.rstrip("/")
    host = request.headers.get("host") or "120.46.81.119"
    scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
    return f"{scheme}://{host}"


@router.post("/route")
async def create_share(
    request: Request,
    payload: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """存储路线，返回 share_id 和分享 URL。"""
    plan = payload.get("plan")
    if not isinstance(plan, dict) or not plan.get("steps"):
        raise HTTPException(status_code=400, detail="无效的路线数据")

    title = str(payload.get("title") or plan.get("intro") or "半日出行路线")[:200]
    plan_json = json.dumps(plan, ensure_ascii=False)
    if len(plan_json.encode("utf-8")) > 500_000:
        raise HTTPException(status_code=400, detail="路线数据过大")

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # 生成唯一 share_id（最多重试 5 次）
            share_id = ""
            for _ in range(5):
                candidate = _gen_share_id()
                cursor.execute("SELECT share_id FROM shared_routes WHERE share_id = %s", (candidate,))
                if not cursor.fetchone():
                    share_id = candidate
                    break
            if not share_id:
                raise HTTPException(status_code=500, detail="生成分享 ID 失败")

            expires_at = datetime.now() + timedelta(days=_SHARE_EXPIRE_DAYS)
            cursor.execute(
                """INSERT INTO shared_routes (share_id, user_id, title, plan_json, expires_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (share_id, current_user["id"], title, plan_json, expires_at),
            )
        conn.commit()
    finally:
        conn.close()

    share_url = f"{_base_url(request)}/app?share={share_id}"
    logger.info("🔗 用户 %s 生成行程分享 %s", current_user.get("name"), share_id)
    return {"code": 200, "data": {"share_id": share_id, "share_url": share_url, "title": title}}


@router.get("/route/{share_id}")
async def get_share(share_id: str):
    """公开获取分享路线（无需登录）。"""
    if not share_id or len(share_id) > 16:
        raise HTTPException(status_code=404, detail="分享链接无效")

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT title, plan_json, expires_at, view_count FROM shared_routes WHERE share_id = %s",
                (share_id,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="行程不存在或已过期")
            expires_at = row.get("expires_at")
            if expires_at and datetime.now() > expires_at:
                raise HTTPException(status_code=410, detail="分享链接已过期")
            # 增加浏览次数（忽略失败）
            try:
                cursor.execute(
                    "UPDATE shared_routes SET view_count = view_count + 1 WHERE share_id = %s",
                    (share_id,),
                )
                conn.commit()
            except Exception:
                pass
        plan = json.loads(row["plan_json"] or "{}")
        return {
            "code": 200,
            "data": {
                "share_id": share_id,
                "title": row["title"],
                "plan": plan,
                "view_count": (row.get("view_count") or 0) + 1,
            },
        }
    finally:
        conn.close()
