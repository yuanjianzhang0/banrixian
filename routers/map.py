# -*- coding: utf-8 -*-
"""Map and POI routes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.parse
import urllib.request
from fastapi import APIRouter, HTTPException, Depends

import pymysql

from core.auth import get_current_user
from core.database import get_db


logger = logging.getLogger("BanrixianAPI")

router = APIRouter(tags=["map", "content"])

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PLACE_IMAGE_DIR = os.path.join(BASE_DIR, "static", "place_images")
AMAP_DETAIL_URL = "https://restapi.amap.com/v3/place/detail"


def _read_amap_key() -> str:
    key = os.getenv("AMAP_WEB_API_KEY", "").strip()
    if key:
        return key
    for name in ("amap_web_api", "amap_api"):
        path = os.path.join(BASE_DIR, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as file:
                return file.read().strip()
    return ""


def _safe_image_key(source: str, source_id: str) -> str:
    raw = f"{source}:{source_id}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _fetch_amap_photo_url(source_id: str) -> str:
    key = _read_amap_key()
    if not key:
        return ""
    params = urllib.parse.urlencode({
        "key": key,
        "id": source_id,
        "extensions": "all",
        "output": "JSON",
    })
    with urllib.request.urlopen(f"{AMAP_DETAIL_URL}?{params}", timeout=12) as response:
        data = json.loads(response.read().decode("utf-8"))
    if str(data.get("status")) != "1":
        return ""
    pois = data.get("pois") or []
    if not pois or not isinstance(pois[0], dict):
        return ""
    photos = pois[0].get("photos") or []
    if not photos or not isinstance(photos[0], dict):
        return ""
    return str(photos[0].get("url") or "").strip()


def _download_image(url: str, image_path: str) -> bool:
    if not url:
        return False
    request = urllib.request.Request(url, headers={"User-Agent": "BanrixianAI/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        content = response.read(2_000_000)
    if len(content) < 128:
        return False
    with open(image_path, "wb") as file:
        file.write(content)
    return True


@router.get("/v1/services/list")
async def get_services_list():
    """Get available services."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM services")
            rows = cursor.fetchall()
            data = []
            for r in rows:
                r["opts"] = json.loads(r["opts"])
                r["desc"] = r.pop("desc_text", "")
                data.append(r)
            return {"code": 200, "data": data}
    except pymysql.Error as e:
        logger.error(f"查询服务列表时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="查询失败")
    finally:
        conn.close()


@router.get("/v1/map/nearby")
async def get_nearby_pois(lat: float = None, lng: float = None, limit: int = 3):
    """Get nearby POIs."""
    limit = max(1, min(int(limit or 3), 3))
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                SELECT 
                    p.*,
                    COALESCE(cs.available, p.total_seats) AS available_seats,
                    COALESCE(cs.queue_count, 0)           AS queue_count,
                    COALESCE(cs.status, 'available')      AS capacity_status,
                    cs.updated_at                         AS capacity_updated_at
                FROM nearby_pois p
                LEFT JOIN place_capacity_status cs ON cs.place_id = p.id
                ORDER BY RAND()
                LIMIT {limit}
            """)
            return {"code": 200, "data": cursor.fetchall()}
    except pymysql.Error as e:
        logger.error(f"查询附近POI时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="查询失败")
    finally:
        conn.close()


@router.get("/v1/map/place-image")
async def get_place_image(
    source: str = "amap",
    source_id: str = "",
    category: str = "",
    current_user: dict = Depends(get_current_user),
):
    """Return a locally cached place image URL. Fetches AMap photo only once."""
    source = (source or "amap").strip().lower()
    source_id = (source_id or "").strip()
    if not source_id:
        return {"code": 200, "data": {"image_url": "", "cached": False, "placeholder": True}}

    os.makedirs(PLACE_IMAGE_DIR, exist_ok=True)
    cache_key = _safe_image_key(source, source_id)
    image_name = f"{cache_key}.jpg"
    image_path = os.path.join(PLACE_IMAGE_DIR, image_name)
    meta_path = os.path.join(PLACE_IMAGE_DIR, f"{cache_key}.json")
    static_url = f"/static/place_images/{image_name}"

    if os.path.exists(image_path):
        return {"code": 200, "data": {"image_url": static_url, "cached": True, "placeholder": False}}

    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as file:
                meta = json.load(file)
            if meta.get("status") == "no_image":
                return {"code": 200, "data": {"image_url": "", "cached": True, "placeholder": True}}
        except Exception:
            pass

    if source != "amap":
        return {"code": 200, "data": {"image_url": "", "cached": False, "placeholder": True}}

    try:
        photo_url = _fetch_amap_photo_url(source_id)
        if photo_url and _download_image(photo_url, image_path):
            with open(meta_path, "w", encoding="utf-8") as file:
                json.dump({"status": "ok", "source": source, "source_id": source_id}, file, ensure_ascii=False)
            return {"code": 200, "data": {"image_url": static_url, "cached": False, "placeholder": False}}
    except Exception as exc:
        logger.warning("地点图片缓存失败 source=%s source_id=%s: %s", source, source_id, exc)

    with open(meta_path, "w", encoding="utf-8") as file:
        json.dump({"status": "no_image", "source": source, "source_id": source_id, "category": category}, file, ensure_ascii=False)
    return {"code": 200, "data": {"image_url": "", "cached": False, "placeholder": True}}


@router.get("/v1/content/hotlist")
async def get_hot_list(current_user: dict = Depends(get_current_user)):
    """Get hot list."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM hot_list ORDER BY RAND() LIMIT 20")
            rows = cursor.fetchall()
            for r in rows:
                r["desc"] = r.pop("desc_text", "")
            return {"code": 200, "data": rows}
    except pymysql.Error as e:
        logger.error(f"查询热榜时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="查询失败")
    finally:
        conn.close()


@router.get("/v1/map/top-routes")
async def get_top_routes(limit: int = 5):
    """Get top routes."""
    limit = max(1, min(int(limit or 5), 5))
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT * FROM ai_routes ORDER BY RAND() LIMIT {limit}")
            rows = cursor.fetchall()
            for r in rows:
                r["route_data"] = json.loads(r["route_data"])
            return {"code": 200, "data": rows}
    except pymysql.Error as e:
        logger.error(f"查询热门路线时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="查询失败")
    finally:
        conn.close()
