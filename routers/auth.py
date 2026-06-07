# -*- coding: utf-8 -*-
"""Authentication routes for user registration, login, and WeChat integration."""

from __future__ import annotations

import json
import time
import uuid
import random
import os
import logging
import hashlib
import hmac
import secrets
from fastapi import APIRouter, HTTPException, Body, UploadFile, File, Form, Request

import pymysql

from core.config import DATABASE_NAME
from core.database import generate_numeric_id, get_db
from core.errors import DatabaseError, ValidationError


logger = logging.getLogger("BanrixianAPI")

router = APIRouter(prefix="/v1/auth", tags=["auth"])

CAPTCHA_AFTER_FAILURES = 1
FAIL_WINDOW_SECONDS = 10 * 60
RATE_WINDOW_SECONDS = 60
RATE_LIMIT_ATTEMPTS = 20
CAPTCHA_EXPIRE_SECONDS = 120
CAPTCHA_TOLERANCE = 7
CAPTCHA_IMAGE_URL = "/assets/captcha-travel-bg-small.png"

_LOGIN_FAILURES: dict[str, list[float]] = {}
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_CAPTCHA_CHALLENGES: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _login_key(request: Request, username: str | None) -> str:
    user = (username or "").strip().lower() or "anonymous"
    return f"{_client_ip(request)}:{user}"


def _prune(bucket: dict[str, list[float]], key: str, window: int) -> list[float]:
    cutoff = _now() - window
    values = [item for item in bucket.get(key, []) if item >= cutoff]
    bucket[key] = values
    return values


def _rate_limit_login(key: str) -> None:
    attempts = _prune(_LOGIN_ATTEMPTS, key, RATE_WINDOW_SECONDS)
    if len(attempts) >= RATE_LIMIT_ATTEMPTS:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    attempts.append(_now())
    _LOGIN_ATTEMPTS[key] = attempts


def _failure_count(key: str) -> int:
    return len(_prune(_LOGIN_FAILURES, key, FAIL_WINDOW_SECONDS))


def _record_failure(key: str) -> int:
    failures = _prune(_LOGIN_FAILURES, key, FAIL_WINDOW_SECONDS)
    failures.append(_now())
    _LOGIN_FAILURES[key] = failures
    return len(failures)


def _clear_failures(key: str) -> None:
    _LOGIN_FAILURES.pop(key, None)


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def _verify_password(raw_password: str, stored_password: str | None) -> bool:
    stored = stored_password or ""
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, salt, digest = stored.split("$", 2)
            actual = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
            return hmac.compare_digest(actual, digest)
        except ValueError:
            return False
    return hmac.compare_digest(raw_password, stored)


def _captcha_error(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _verify_captcha(payload: dict | None, consume: bool = True) -> None:
    if not isinstance(payload, dict):
        raise _captcha_error("请先完成滑块验证")
    captcha_id = str(payload.get("id") or "").strip()
    challenge = _CAPTCHA_CHALLENGES.get(captcha_id)
    if not challenge:
        raise _captcha_error("验证码已失效，请重新滑动")
    if challenge.get("used") or challenge.get("expires_at", 0) < _now():
        _CAPTCHA_CHALLENGES.pop(captcha_id, None)
        raise _captcha_error("验证码已过期，请重新滑动")

    try:
        x = float(payload.get("x"))
        elapsed = int(payload.get("elapsed") or 0)
    except (TypeError, ValueError):
        raise _captcha_error("验证码参数不完整")

    trail = payload.get("trail")
    if not isinstance(trail, list):
        trail = []

    target = float(challenge["target"])
    if abs(x - target) > CAPTCHA_TOLERANCE:
        raise _captcha_error("滑块位置不正确，请重试")
    if elapsed < 450 or elapsed > 30_000:
        raise _captcha_error("滑动行为异常，请重试")
    if len(trail) < 3:
        raise _captcha_error("滑动轨迹不足，请重试")
    xs = []
    for point in trail:
        if isinstance(point, dict):
            try:
                xs.append(float(point.get("x")))
            except (TypeError, ValueError):
                pass
    # 允许用户滑过目标后回退到正确位置；只校验轨迹曾经接近目标区域。
    if xs and max(xs) < target * 0.75:
        raise _captcha_error("滑动轨迹异常，请重试")

    if consume:
        challenge["used"] = True


@router.post("/captcha")
async def create_slider_captcha():
    """Create a lightweight server-verified slider captcha challenge."""
    active_challenges = {
        cid: data for cid, data in _CAPTCHA_CHALLENGES.items()
        if data.get("expires_at", 0) >= _now() and not data.get("used")
    }
    _CAPTCHA_CHALLENGES.clear()
    _CAPTCHA_CHALLENGES.update(active_challenges)
    captcha_id = secrets.token_urlsafe(18)
    target = random.randint(72, 242)
    piece_top = random.randint(28, 82)
    _CAPTCHA_CHALLENGES[captcha_id] = {
        "target": target,
        "expires_at": _now() + CAPTCHA_EXPIRE_SECONDS,
        "used": False,
    }
    return {
        "code": 200,
        "data": {
            "id": captcha_id,
            "target": target,
            "width": 360,
            "height": 168,
            "pieceSize": 46,
            "pieceTop": piece_top,
            "image": CAPTCHA_IMAGE_URL,
            "tolerance": CAPTCHA_TOLERANCE,
            "expiresIn": CAPTCHA_EXPIRE_SECONDS,
        },
    }


@router.post("/register")
async def user_register(request: Request, payload: dict = Body(...)):
    """Register a new user with username and password."""
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    name = payload.get("name") or f"闲友_{str(uuid.uuid4())[:4]}"
    captcha_payload = payload.get("captcha")
    _verify_captcha(captcha_payload, consume=False)
    
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    if len(str(password)) < 6:
        raise HTTPException(status_code=400, detail="密码至少需要 6 位")
        
    conn = get_db()
    try:
        user_id = str(uuid.uuid4())
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, password, name, pts, tags) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, username, _hash_password(str(password)), name, 100, json.dumps(["☕ 新人体验官"]))
            )
        _verify_captcha(captcha_payload, consume=True)
        conn.commit()
        return {"code": 200, "message": "注册成功"}
    except pymysql.err.IntegrityError:
        raise HTTPException(status_code=400, detail="该用户名已被占用")
    except pymysql.Error as e:
        logger.error(f"注册过程中数据库错误: {e}")
        raise HTTPException(status_code=500, detail="注册失败")
    finally:
        conn.close()


@router.post("/login")
async def user_login(request: Request, payload: dict = Body(...)):
    """Login with username and password."""
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    key = _login_key(request, username)
    _rate_limit_login(key)
    captcha_payload = payload.get("captcha")
    _verify_captcha(captcha_payload, consume=False)
    
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            if not user or not _verify_password(password, user.get("password")):
                _record_failure(key)
                raise HTTPException(status_code=400, detail="用户名或密码错误，请重新输入账号和密码")

            if not str(user.get("password") or "").startswith("pbkdf2_sha256$"):
                cursor.execute("UPDATE users SET password = %s WHERE id = %s", (_hash_password(password), user["id"]))
            _verify_captcha(captcha_payload, consume=True)
            
            token = "tk_" + uuid.uuid4().hex
            cursor.execute("INSERT INTO user_tokens (token, user_id, expire_time) VALUES (%s, %s, %s)", 
                           (token, user["id"], time.time() + 86400 * 7))
        conn.commit()
        _clear_failures(key)
        
        user["tags"] = json.loads(user["tags"]) if user.get("tags") else []
        if "password" in user:
            del user["password"]
        
        return {"code": 200, "message": "登录成功", "data": {"token": token, "user": user}}
    except pymysql.Error as e:
        logger.error(f"登录过程中数据库错误: {e}")
        raise HTTPException(status_code=500, detail="登录失败")
    finally:
        conn.close()


@router.post("/wx-login")
async def wx_login_check(payload: dict = Body(...)):
    """Check or create WeChat user."""
    openid = payload.get("openid")
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE openid = %s", (openid,))
            user = cursor.fetchone()
            
            if not user:
                return {"code": 200, "data": {"isNewUser": True, "openid": openid}}
                
            token = "tk_wx_" + str(int(time.time())) + str(random.randint(100, 999))
            cursor.execute("INSERT INTO user_tokens (token, user_id, expire_time) VALUES (%s, %s, %s)", 
                           (token, user["id"], time.time() + 86400 * 7))
        conn.commit()
        
        user["tags"] = json.loads(user["tags"]) if user.get("tags") else []
        if "password" in user:
            del user["password"]
        return {"code": 200, "data": {"isNewUser": False, "token": token, "user": user}}
    except pymysql.Error as e:
        logger.error(f"微信登录过程中数据库错误: {e}")
        raise HTTPException(status_code=500, detail="登录失败")
    finally:
        conn.close()


@router.post("/wx-upload-avatar")
async def wx_register_save_profile(
    file: UploadFile = File(...),
    openid: str = Form(...),
    nickName: str = Form(...)
):
    """Register new WeChat user with avatar."""
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    AVATAR_DIR = os.path.join(BASE_DIR, "../static/avatars")
    os.makedirs(AVATAR_DIR, exist_ok=True)
    
    file_extension = os.path.splitext(file.filename)[1] or ".png"
    filename = f"{openid}_{int(time.time())}{file_extension}"
    file_path = os.path.join(AVATAR_DIR, filename)
    
    with open(file_path, "wb") as f:
        f.write(await file.read())
    
    real_avatar_url = f"http://wxapi.jufu.vip/static/avatars/{filename}"
    user_id = str(generate_numeric_id())
    
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, openid, name, avatar, city, pts, tags) VALUES (%s, %s, %s, %s, %s, %s, %s)", 
                (user_id, openid, nickName, real_avatar_url, "北京", 200, json.dumps(["💼 商务宴请", "🍷 高端餐饮"]))
            )
            token = "tk_wx_" + str(int(time.time())) + str(random.randint(100, 999))
            cursor.execute("INSERT INTO user_tokens (token, user_id, expire_time) VALUES (%s, %s, %s)", 
                           (token, user_id, time.time() + 86400 * 7))
            
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone()
            
        conn.commit()
        user["tags"] = json.loads(user["tags"])
        return {"code": 200, "data": {"token": token, "user": user}}
    except pymysql.Error as e:
        logger.error(f"微信注册过程中数据库错误: {e}")
        raise HTTPException(status_code=500, detail="注册失败")
    finally:
        conn.close()
