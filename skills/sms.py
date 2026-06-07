# -*- coding: utf-8 -*-
"""SMS skill for +86 mobile numbers.

Default provider is mock/dry-run so demos can show execution without sending
real SMS. Production can set SMS_PROVIDER=webhook and point SMS_WEBHOOK_URL to
an approved SMS gateway wrapper.
"""

from __future__ import annotations

import re
import time

from core.config import (
    SMS_DRY_RUN,
    SMS_PROVIDER,
    SMS_TIMEOUT_SECONDS,
    SMS_WEBHOOK_TOKEN,
    SMS_WEBHOOK_URL,
)


_CN_MOBILE_RE = re.compile(r"^(?:\+?86)?1[3-9]\d{9}$")


def normalize_cn_phone(phone: str) -> str:
    """Normalize a mainland China mobile number to +86XXXXXXXXXXX."""
    cleaned = re.sub(r"[\s\-()（）]", "", str(phone or ""))
    if cleaned.startswith("0086"):
        cleaned = "+86" + cleaned[4:]
    if cleaned.startswith("86") and not cleaned.startswith("+86"):
        cleaned = "+" + cleaned
    if cleaned.startswith("+86"):
        national = cleaned[3:]
    else:
        national = cleaned
        cleaned = "+86" + cleaned
    if not _CN_MOBILE_RE.fullmatch(cleaned):
        raise ValueError("仅支持中国大陆 +86 手机号，格式应为 +8613xxxxxxxxx")
    return "+86" + national


def mask_phone(phone: str) -> str:
    normalized = normalize_cn_phone(phone)
    return f"{normalized[:5]}****{normalized[-4:]}"


def _normalize_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        raise ValueError("短信内容不能为空")
    if len(text) > 300:
        raise ValueError("短信内容不能超过300字")
    return text


async def send_sms(phone: str, message: str, scene: str = "plan_notify", request_id: str | None = None) -> dict:
    normalized_phone = normalize_cn_phone(phone)
    content = _normalize_message(message)
    provider = SMS_PROVIDER or "mock"
    request_id = request_id or f"sms_{int(time.time() * 1000)}"

    if provider in {"mock", "local", "disabled", "none"} or SMS_DRY_RUN:
        return {
            "type": "sms",
            "status": "mock_success",
            "provider": "mock",
            "request_id": request_id,
            "phone": mask_phone(normalized_phone),
            "scene": scene,
            "message": f"短信已生成（mock）：{content}",
        }

    if provider != "webhook":
        return {
            "type": "sms",
            "status": "unavailable",
            "provider": provider,
            "request_id": request_id,
            "phone": mask_phone(normalized_phone),
            "scene": scene,
            "message": f"短信服务未启用或 provider 不支持：{provider}",
        }

    if not SMS_WEBHOOK_URL:
        return {
            "type": "sms",
            "status": "unavailable",
            "provider": "webhook",
            "request_id": request_id,
            "phone": mask_phone(normalized_phone),
            "scene": scene,
            "message": "短信 webhook 地址未配置",
        }

    try:
        import httpx

        headers = {"Content-Type": "application/json"}
        if SMS_WEBHOOK_TOKEN:
            headers["Authorization"] = f"Bearer {SMS_WEBHOOK_TOKEN}"
        async with httpx.AsyncClient(timeout=SMS_TIMEOUT_SECONDS, trust_env=False) as client:
            response = await client.post(
                SMS_WEBHOOK_URL,
                headers=headers,
                json={
                    "phone": normalized_phone,
                    "message": content,
                    "scene": scene,
                    "request_id": request_id,
                },
            )
            response.raise_for_status()
            payload = response.json() if response.content else {}
    except Exception as exc:
        return {
            "type": "sms",
            "status": "failed",
            "provider": "webhook",
            "request_id": request_id,
            "phone": mask_phone(normalized_phone),
            "scene": scene,
            "message": f"短信发送失败：{exc}",
        }

    return {
        "type": "sms",
        "status": "sent",
        "provider": "webhook",
        "request_id": request_id,
        "phone": mask_phone(normalized_phone),
        "scene": scene,
        "message": "短信已发送",
        "raw": payload if isinstance(payload, dict) else {},
    }
