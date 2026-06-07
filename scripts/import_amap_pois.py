#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import real POIs from AMap Web Service into places.

Reads the API key from AMAP_WEB_API_KEY, or from a local file named
``amap_web_api`` / ``amap_api`` when present. Keep keys out of frontend code.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
import sys
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.database import get_db, init_db


AMAP_TEXT_SEARCH_URL = "https://restapi.amap.com/v3/place/text"
DEFAULT_KEYWORDS = [
    "餐厅", "咖啡", "商场", "公园", "博物馆", "展览", "亲子乐园", "游乐场",
    "美术馆", "电影院", "书店", "茶馆", "烤鸭", "火锅", "景点",
]


def _read_key() -> str:
    key = os.getenv("AMAP_WEB_API_KEY", "").strip()
    if key:
        return key
    for name in ("amap_web_api", "amap_api"):
        path = Path(name)
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    raise SystemExit("Missing AMAP_WEB_API_KEY or local amap_web_api file")


def _category_from_type(type_text: str, keyword: str) -> str:
    text = f"{type_text} {keyword}"
    if any(w in text for w in ("餐饮", "美食", "中餐", "火锅", "烤鸭", "小吃", "咖啡", "茶")):
        return "餐厅" if "咖啡" not in text and "茶" not in text else "咖啡"
    if any(w in text for w in ("购物", "商场", "商城")):
        return "商场"
    if any(w in text for w in ("博物馆", "美术馆", "展览", "科教文化")):
        return "文化"
    if any(w in text for w in ("风景", "公园", "景点", "游乐", "亲子")):
        return "景点"
    if any(w in text for w in ("电影院", "影城")):
        return "娱乐"
    return "地点"


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join(str(v) for v in value if v)
    return ""


def _fetch_page(key: str, city: str, keyword: str, page: int, offset: int) -> list[dict]:
    params = {
        "key": key,
        "keywords": keyword,
        "city": city,
        "citylimit": "true",
        "offset": str(offset),
        "page": str(page),
        "extensions": "all",
        "output": "JSON",
    }
    url = f"{AMAP_TEXT_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    if str(data.get("status")) != "1":
        raise RuntimeError(f"AMap error for {keyword} page {page}: {data.get('info') or data}")
    pois = data.get("pois") or []
    return pois if isinstance(pois, list) else []


def _completed_runs(city: str, keywords: list[str], pages: int, offset: int, force: bool) -> set[str]:
    if force or not keywords:
        return set()
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            placeholders = ", ".join(["%s"] * len(keywords))
            cursor.execute(f"""
                SELECT keyword
                FROM poi_import_runs
                WHERE source = 'amap'
                  AND city = %s
                  AND status = 'success'
                  AND pages >= %s
                  AND offset_count >= %s
                  AND keyword IN ({placeholders})
            """, (city, pages, offset, *keywords))
            return {str(row.get("keyword") or "") for row in cursor.fetchall()}
    finally:
        conn.close()


def _record_import_run(
    city: str,
    keyword: str,
    pages: int,
    offset: int,
    imported_count: int,
    status: str,
    error_text: str = "",
) -> None:
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO poi_import_runs
                (source, city, keyword, pages, offset_count, imported_count, status, error_text, updated_at)
                VALUES ('amap', %s, %s, %s, %s, %s, %s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    pages = GREATEST(pages, VALUES(pages)),
                    offset_count = GREATEST(offset_count, VALUES(offset_count)),
                    imported_count = VALUES(imported_count),
                    status = VALUES(status),
                    error_text = VALUES(error_text),
                    updated_at = NOW()
            """, (city, keyword, pages, offset, imported_count, status, error_text[:1000]))
        conn.commit()
    finally:
        conn.close()


def _normalize_poi(poi: dict, city: str, keyword: str) -> dict | None:
    name = str(poi.get("name") or "").strip()
    location = str(poi.get("location") or "").strip()
    if not name or "," not in location:
        return None
    lng_s, lat_s = location.split(",", 1)
    try:
        lng = float(lng_s)
        lat = float(lat_s)
    except ValueError:
        return None
    biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
    rating = str(biz_ext.get("rating") or poi.get("biz_ext.rating") or "").strip()
    cost = str(biz_ext.get("cost") or "").strip()
    type_text = str(poi.get("type") or "").strip()
    district = str(poi.get("adname") or "").strip()
    business_area = str(poi.get("business_area") or "").strip()
    address = _first_text(poi.get("address")).strip()
    tel = _first_text(poi.get("tel")).strip()
    tags = [item for item in [keyword, type_text, district, business_area] if item]
    price_range = f"¥{cost}/人" if cost and cost.replace(".", "", 1).isdigit() else ""
    desc_parts = [type_text, business_area, tel and f"电话 {tel}"]
    return {
        "source_id": str(poi.get("id") or "").strip() or f"amap:{city}:{name}:{lng:.6f}:{lat:.6f}",
        "name": name[:128],
        "category": _category_from_type(type_text, keyword),
        "keyword": keyword[:64],
        "address": address[:255],
        "open_hours": "",
        "price_range": price_range[:32],
        "score": rating[:8] if rating else "",
        "desc_text": "；".join(p for p in desc_parts if p)[:255],
        "lng": lng,
        "lat": lat,
        "avg_price": int(float(cost)) if cost and cost.replace(".", "", 1).isdigit() else 0,
        "city": city,
        "district": district,
        "business_area": business_area[:128],
        "tags": json.dumps(tags, ensure_ascii=False),
        "phone": tel[:128],
    }


def _random_capacity_for_place(category: str) -> tuple[int, int, str]:
    category = str(category or "")
    if category in {"餐厅", "咖啡", "酒吧"}:
        available = random.randint(0, 36)
        queue_count = random.randint(0, 18 if available <= 6 else 6)
    elif category in {"文化", "景点", "娱乐"}:
        available = random.randint(8, 160)
        queue_count = random.randint(0, 25)
    else:
        available = random.randint(3, 80)
        queue_count = random.randint(0, 12)
    status = "full" if available <= 0 else "available"
    return available, queue_count, status


def _ensure_capacity_status(cursor, place_id: int, category: str) -> None:
    cursor.execute("SELECT place_id FROM place_capacity_status WHERE place_id = %s LIMIT 1", (place_id,))
    if cursor.fetchone():
        return
    available, queue_count, status = _random_capacity_for_place(category)
    cursor.execute("""
        INSERT INTO place_capacity_status
        (place_id, available, queue_count, status, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
    """, (place_id, available, queue_count, status))


def _upsert_places(rows: list[dict], replace_city: bool, city: str) -> tuple[int, int]:
    if not rows and not replace_city:
        return 0, 0
    conn = get_db()
    try:
        inserted = 0
        updated = 0
        with conn.cursor() as cursor:
            if replace_city:
                cursor.execute("DELETE FROM place_capacity_status WHERE place_id IN (SELECT id FROM places WHERE city = %s)", (city,))
                cursor.execute("DELETE FROM places WHERE city = %s", (city,))
            for row in rows:
                cursor.execute("SELECT id FROM places WHERE source = 'amap' AND source_id = %s LIMIT 1", (row["source_id"],))
                existing = cursor.fetchone()
                payload = (
                    row["name"], row["category"], row["keyword"], row["address"], row["open_hours"],
                    row["price_range"], row["score"], row["desc_text"], row["lng"], row["lat"],
                    300, row["avg_price"], row["city"], "amap", row["source_id"],
                    row["district"], row["business_area"], row["tags"], row["phone"],
                )
                if existing:
                    cursor.execute("""
                        UPDATE places
                        SET name=%s, category=%s, keyword=%s, address=%s, open_hours=%s,
                            price_range=%s, score=%s, desc_text=%s, lng=%s, lat=%s,
                            total_seats=%s, avg_price=%s, city=%s, source=%s, source_id=%s,
                            district=%s, business_area=%s, tags=%s, phone=%s, updated_at=NOW()
                        WHERE id=%s
                    """, payload + (existing["id"],))
                    _ensure_capacity_status(cursor, int(existing["id"]), row["category"])
                    updated += 1
                else:
                    cursor.execute("""
                        INSERT INTO places
                        (name, category, keyword, address, open_hours, price_range, score, desc_text,
                         lng, lat, total_seats, avg_price, city, source, source_id, district,
                         business_area, tags, phone, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """, payload)
                    _ensure_capacity_status(cursor, int(cursor.lastrowid), row["category"])
                    inserted += 1
        conn.commit()
        return inserted, updated
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="北京")
    parser.add_argument("--keywords", default=",".join(DEFAULT_KEYWORDS))
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--offset", type=int, default=20)
    parser.add_argument("--replace-city", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore city+keyword import cache and request AMap again.")
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()

    key = _read_key()
    init_db()
    keywords = [item.strip() for item in args.keywords.split(",") if item.strip()]
    pages = max(1, args.pages)
    offset = max(1, min(args.offset, 25))
    completed = _completed_runs(args.city, keywords, pages, offset, args.force)
    seen: set[str] = set()
    rows: list[dict] = []
    keyword_counts: dict[str, int] = {}
    skipped_keywords: list[str] = []
    for keyword in keywords:
        if keyword in completed:
            skipped_keywords.append(keyword)
            print(f"skip cached {args.city} {keyword}")
            continue
        before_count = len(rows)
        try:
            for page in range(1, pages + 1):
                pois = _fetch_page(key, args.city, keyword, page, offset)
                print(f"{args.city} {keyword} page {page}: {len(pois)}")
                for poi in pois:
                    row = _normalize_poi(poi, args.city, keyword)
                    if not row or row["source_id"] in seen:
                        continue
                    seen.add(row["source_id"])
                    rows.append(row)
                time.sleep(max(0.0, args.sleep))
            keyword_counts[keyword] = len(rows) - before_count
        except Exception as exc:
            _record_import_run(args.city, keyword, pages, offset, len(rows) - before_count, "failed", str(exc))
            raise

    print(f"normalized: {len(rows)}; skipped_cached: {len(skipped_keywords)}")
    if args.dry_run:
        print(json.dumps(rows[:5], ensure_ascii=False, indent=2))
        return 0
    inserted, updated = _upsert_places(rows, replace_city=args.replace_city, city=args.city)
    for keyword, count in keyword_counts.items():
        _record_import_run(args.city, keyword, pages, offset, count, "success")
    print({"inserted": inserted, "updated": updated, "city": args.city})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
