#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill random capacity rows for places missing seat status."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.database import get_db
from scripts.import_amap_pois import _random_capacity_for_place


def main() -> int:
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT p.id, p.category
                FROM places p
                LEFT JOIN place_capacity_status cs ON cs.place_id = p.id
                WHERE cs.place_id IS NULL
            """)
            rows = cursor.fetchall()
            for row in rows:
                available, queue_count, status = _random_capacity_for_place(row.get("category") or "")
                cursor.execute("""
                    INSERT INTO place_capacity_status
                    (place_id, available, queue_count, status, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                """, (row["id"], available, queue_count, status))
        conn.commit()
        print(f"backfilled={len(rows)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
