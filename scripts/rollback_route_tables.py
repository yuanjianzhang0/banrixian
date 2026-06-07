#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Restore route-related tables from a JSON backup created before real-route changes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from core.database import get_db


DEFAULT_BACKUP = Path("backups/20260601-231631/db_route_tables.json")
TABLES = ("ai_routes", "places", "nearby_pois", "hot_list", "place_capacity_status")


def main() -> int:
    backup_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BACKUP
    data = json.loads(backup_path.read_text(encoding="utf-8"))

    conn = get_db()
    try:
      with conn.cursor() as cursor:
        for table in TABLES:
          rows = data.get(table)
          if not isinstance(rows, list):
            print(f"skip {table}: no list data")
            continue
          cursor.execute(f"TRUNCATE TABLE {table}")
          if not rows:
            print(f"restored {table}: 0 rows")
            continue
          columns = list(rows[0].keys())
          placeholders = ", ".join(["%s"] * len(columns))
          column_sql = ", ".join(f"`{column}`" for column in columns)
          sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})"
          cursor.executemany(sql, [[row.get(column) for column in columns] for row in rows])
          print(f"restored {table}: {len(rows)} rows")
      conn.commit()
    finally:
      conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
