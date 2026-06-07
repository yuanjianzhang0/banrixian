# -*- coding: utf-8 -*-
"""Notification routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from core.auth import get_current_user
from skills.sms import send_sms


logger = logging.getLogger("BanrixianAPI")
router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


@router.post("/sms/send")
async def send_sms_notification(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """Send a +86 SMS notification or return a mock receipt.

    payload:
    {
      "phone": "+8613800138000",
      "message": "下午2点出发，先去...",
      "scene": "plan_notify"
    }
    """
    phone = (payload.get("phone") or "").strip()
    message = (payload.get("message") or payload.get("content") or "").strip()
    scene = (payload.get("scene") or "plan_notify").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone 不能为空")
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")

    try:
        result = await send_sms(phone=phone, message=message, scene=scene)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info("📩 用户 %s 触发短信 scene=%s status=%s", current_user.get("name"), scene, result.get("status"))
    return {"code": 200, "data": result}
