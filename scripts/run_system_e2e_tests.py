#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end system tests for the Banrixian stack.

The script creates a temporary user, exercises real HTTP APIs, calls the live
chat/LLM path, writes a markdown log, and removes temporary data afterwards.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import get_db


DEFAULT_BASE_URL = "http://127.0.0.1:3000"
DEFAULT_LOG = ROOT / "test_logs" / "system_e2e_20260602.md"
FRONTEND_INDEX = ROOT / "banrixian_web" / "index.html"
NGINX_INDEX = Path("/var/www/banrixian_web/index.html")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class E2ERunner:
    def __init__(self, base_url: str, log_path: Path, keep_user: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.log_path = log_path
        self.keep_user = keep_user
        suffix = datetime.now().strftime("%Y%m%d%H%M%S")
        self.username = f"e2e_{suffix}"
        self.password = "codex_e2e_pass"
        self.token = ""
        self.user_id = ""
        self.results: list[CheckResult] = []
        self.plan: dict[str, Any] = {}
        self.provider_events: list[dict[str, Any]] = []
        self.created_order_ids: list[str] = []

    def add(self, name: str, ok: bool, detail: str = "", data: dict[str, Any] | None = None) -> None:
        self.results.append(CheckResult(name=name, status="PASS" if ok else "FAIL", detail=detail, data=data or {}))

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        auth: bool = False,
        timeout: int = 30,
    ) -> tuple[int, dict[str, Any] | str]:
        url = self.base_url + self._safe_path(path)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                text = response.read().decode("utf-8")
                try:
                    return response.status, json.loads(text) if text else {}
                except json.JSONDecodeError:
                    return response.status, text
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(text) if text else {}
            except json.JSONDecodeError:
                return exc.code, text

    def stream_chat(self, text: str, payload_extra: dict[str, Any] | None = None, timeout: int = 90) -> dict[str, Any]:
        payload = {"text": text, **(payload_extra or {})}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/v1/chat/send",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        events: list[dict[str, Any]] = []
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        for line in raw.splitlines():
            if not line.startswith("data: "):
                continue
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                events.append({"type": "parse_error", "raw": line[6:]})
        done = next((event.get("data") for event in reversed(events) if event.get("type") == "done"), None)
        errors = [event for event in events if event.get("type") == "error"]
        provider_events = [
            {"provider": event.get("provider"), "model": event.get("model"), "content": event.get("content")}
            for event in events
            if event.get("type") == "status" and (event.get("provider") or event.get("model"))
        ]
        return {"events": events, "done": done, "errors": errors, "provider_events": provider_events}

    def run_command(self, name: str, cmd: list[str]) -> None:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=90)
        detail = (proc.stdout + proc.stderr).strip()
        self.add(name, proc.returncode == 0, detail[:800], {"cmd": cmd, "returncode": proc.returncode})

    def check_static_and_compile(self) -> None:
        self.run_command("python_compile_core", ["python", "-m", "compileall", "-q", "agent", "routers", "core", "scripts"])
        self.run_command("frontend_chat_js_syntax", ["node", "--check", "banrixian_web/js/views/chat.js"])
        index_text = FRONTEND_INDEX.read_text(encoding="utf-8")
        version_ok = "app-version" in index_text and "?v=" in index_text
        self.add("frontend_version_visible", version_ok, "index.html has app-version and cache-busting query")
        if NGINX_INDEX.exists():
            nginx_text = NGINX_INDEX.read_text(encoding="utf-8", errors="replace")
            version = self._extract_version(index_text)
            self.add("nginx_static_version_synced", bool(version and version in nginx_text), f"version={version}")
        else:
            self.add("nginx_static_version_synced", False, f"missing {NGINX_INDEX}")

    def check_database(self) -> None:
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM places")
                place_count = int(cursor.fetchone()["n"])
                cursor.execute("""
                    SELECT COUNT(*) AS n
                    FROM places p
                    LEFT JOIN place_capacity_status cs ON cs.place_id = p.id
                    WHERE cs.place_id IS NULL OR cs.available IS NULL
                """)
                missing_capacity = int(cursor.fetchone()["n"])
                cursor.execute("SELECT city, COUNT(*) AS n FROM places GROUP BY city ORDER BY n DESC LIMIT 5")
                city_rows = cursor.fetchall()
            self.add("db_places_available", place_count > 0, f"places={place_count}", {"places": place_count})
            self.add("db_capacity_complete", missing_capacity == 0, f"missing_capacity={missing_capacity}")
            self.add("db_city_distribution", bool(city_rows), json.dumps(city_rows, ensure_ascii=False))
        finally:
            conn.close()

    def auth_and_user_flow(self) -> None:
        status, data = self.request("POST", "/v1/auth/register", {"username": self.username, "password": self.password, "name": "E2E测试"})
        self.add("auth_register", status == 200 and isinstance(data, dict) and data.get("code") == 200, str(data))
        status, dup = self.request("POST", "/v1/auth/register", {"username": self.username, "password": self.password})
        self.add("auth_duplicate_rejected", status == 400, str(dup))
        status, login = self.request("POST", "/v1/auth/login", {"username": self.username, "password": self.password})
        ok = status == 200 and isinstance(login, dict) and login.get("data", {}).get("token")
        self.token = login.get("data", {}).get("token", "") if isinstance(login, dict) else ""
        self.user_id = login.get("data", {}).get("user", {}).get("id", "") if isinstance(login, dict) else ""
        self.add("auth_login", ok, f"user_id={self.user_id}")

        status, profile = self.request("GET", "/v1/user/profile", auth=True)
        self.add("user_profile_get", status == 200 and isinstance(profile, dict) and "password" not in str(profile.get("data")), str(profile)[:300])
        status, update = self.request("PUT", "/v1/user/profile", {"name": "E2E用户", "city": "北京", "phone": "13800000000", "tags": ["测试用户"]}, auth=True)
        self.add("user_profile_update", status == 200 and isinstance(update, dict) and update.get("code") == 200, str(update))
        status, sign = self.request("POST", "/v1/user/sign", {}, auth=True)
        self.add("user_sign", status == 200 and isinstance(sign, dict) and sign.get("data", {}).get("bonusPoints") == 50, str(sign))

    def family_flow(self) -> None:
        status, added = self.request("POST", "/v1/user/family", {"relation": "儿子", "avatar": "👦", "tags": ["5岁", "喜欢恐龙"]}, auth=True)
        member_id = added.get("data", {}).get("id") if isinstance(added, dict) else None
        self.add("family_add", status == 200 and bool(member_id), str(added))
        status, patched = self.request("PATCH", f"/v1/user/family/{member_id}/tags", {"tags": ["不能久站", "喜欢科普"]}, auth=True)
        tags = patched.get("data", {}).get("tags", []) if isinstance(patched, dict) else []
        self.add("family_patch_tags", status == 200 and "不能久站" in tags and "喜欢恐龙" in tags, str(patched))
        status, updated = self.request("PUT", f"/v1/user/family/{member_id}", {"relation": "儿子", "avatar": "👦", "tags": ["5岁"]}, auth=True)
        self.add("family_update_replace_tags", status == 200 and updated.get("data", {}).get("tags") == ["5岁"], str(updated))
        status, family = self.request("GET", "/v1/user/family", auth=True)
        self.add("family_list", status == 200 and isinstance(family, dict) and len(family.get("data", [])) >= 1, str(family)[:300])
        status, deleted = self.request("DELETE", f"/v1/user/family/{member_id}", auth=True)
        self.add("family_delete", status == 200 and isinstance(deleted, dict), str(deleted))

    def public_and_map_flow(self) -> None:
        checks = [
            ("services_list", "GET", "/v1/services/list", False),
            ("nearby_pois", "GET", "/v1/map/nearby?limit=3", False),
            ("top_routes", "GET", "/v1/map/top-routes?limit=5", False),
            ("hotlist", "GET", "/v1/content/hotlist", True),
            ("place_image_placeholder", "GET", "/v1/map/place-image?source=amap&source_id=", True),
            ("weather_current", "GET", "/v1/weather/current?city=北京", True),
        ]
        for name, method, path, auth in checks:
            status, data = self.request(method, path, auth=auth, timeout=45)
            ok = status == 200 and isinstance(data, dict) and data.get("code") == 200
            self.add(name, ok, str(data)[:500])

    def memory_flow(self) -> None:
        status, memory = self.request("GET", "/v1/agent/memory", auth=True)
        self.add("memory_snapshot", status == 200 and isinstance(memory, dict) and "sessionHistory" in memory.get("data", {}), str(memory)[:400])
        status, cleared = self.request("POST", "/v1/agent/memory/session/clear", {}, auth=True)
        self.add("memory_session_clear", status == 200 and isinstance(cleared, dict) and cleared.get("message") == "session cleared", str(cleared))

    def chat_and_llm_flow(self) -> None:
        chat = self.stream_chat("你好，先聊一句，不要规划")
        done = chat.get("done") or {}
        self.add("chat_text_mode", bool(done and done.get("type") == "text"), json.dumps(done, ensure_ascii=False)[:500])

        plan_run = self.stream_chat("周末约会，下午3点开始，想看展或逛公园", {"city": "北京"})
        self.provider_events = plan_run.get("provider_events") or []
        self.plan = plan_run.get("done") or {}
        steps = [step for step in self.plan.get("steps") or [] if isinstance(step, dict)]
        food_count = self._count_food(steps)
        has_provider = bool(self.provider_events)
        real_provider = any((item.get("provider") or "") not in {"", "local"} for item in self.provider_events)
        self.add("llm_provider_reported", has_provider, json.dumps(self.provider_events, ensure_ascii=False))
        self.add("llm_real_provider_used", real_provider, json.dumps(self.provider_events, ensure_ascii=False))
        self.add("plan_generated", self.plan.get("type") == "plan" and len(steps) >= 1, json.dumps({"steps": [s.get("name") for s in steps]}, ensure_ascii=False))
        self.add("plan_activity_not_all_food", bool(steps and food_count < len(steps)), f"food={food_count}, steps={len(steps)}")
        self.add("plan_steps_have_details", all(s.get("address") is not None and s.get("source_id") is not None for s in steps), json.dumps(steps[:2], ensure_ascii=False)[:800])

        negative = self.stream_chat("下午3点到5点，想看展或逛公园，不吃饭", {"city": "北京"})
        negative_plan = negative.get("done") or {}
        neg_steps = [step for step in negative_plan.get("steps") or [] if isinstance(step, dict)]
        neg_food = self._count_food(neg_steps)
        tp = negative_plan.get("time_plan") or {}
        self.add("plan_no_meal_respected", bool(neg_steps and neg_food == 0), json.dumps({"steps": [s.get("name") for s in neg_steps], "food": neg_food}, ensure_ascii=False))
        self.add("plan_time_range_respected", tp.get("start") == "15:00" and tp.get("end") == "17:00", json.dumps(tp, ensure_ascii=False))

        reserve = self.stream_chat("今晚约会吃饭，帮我预约一家餐厅", {"city": "北京"})
        reserve_plan = reserve.get("done") or {}
        actions = reserve_plan.get("actions") or []
        self.add("plan_reservation_action", any(a.get("type") == "reserve" and a.get("status") == "pending" for a in actions), json.dumps(actions, ensure_ascii=False)[:800])
        self.add("plan_no_mock_failed_reservation", not any(a.get("status") == "mock_failed" for a in actions), json.dumps(actions, ensure_ascii=False)[:800])

    def replace_and_order_flow(self) -> None:
        steps = [step for step in self.plan.get("steps") or [] if isinstance(step, dict)]
        if not steps:
            self.add("replace_step", False, "no plan steps available")
            self.add("confirm_reservations", False, "no plan steps available")
            return
        first = steps[0]
        status, replaced = self.request(
            "POST",
            "/v1/chat/replace-step",
            {"plan_steps": steps, "step_index": 0, "category": first.get("category", ""), "keyword": first.get("keyword", ""), "profile_text": "周末约会看展", "city": "北京"},
            auth=True,
        )
        new_step = replaced.get("data", {}).get("step", {}) if isinstance(replaced, dict) else {}
        self.add("replace_step", status == 200 and new_step.get("name") and new_step.get("name") != first.get("name"), str(replaced)[:800])

        reserve_step = steps[-1]
        status, confirmed = self.request(
            "POST",
            "/v1/orders/confirm-reservations",
            {"plan_summary": "E2E路线预约", "reservations": [{"place_name": reserve_step.get("name"), "time": reserve_step.get("time"), "date": reserve_step.get("date"), "people_count": 2, "price": reserve_step.get("price_range") or "待结算"}]},
            auth=True,
        )
        orders = confirmed.get("data", {}).get("orders", []) if isinstance(confirmed, dict) else []
        self.created_order_ids.extend([str(order.get("id")) for order in orders if order.get("id")])
        self.add("confirm_reservations", status == 200 and len(orders) == 1, str(confirmed))
        status, order_list = self.request("GET", "/v1/orders?status=all", auth=True)
        self.add("orders_list_contains_created", status == 200 and any(o.get("id") in self.created_order_ids for o in order_list.get("data", [])), str(order_list)[:800])
        for order_id in list(self.created_order_ids):
            status, deleted = self.request("DELETE", f"/v1/orders/{urllib.parse.quote(order_id)}", auth=True)
            self.add(f"order_delete_{order_id}", status == 200, str(deleted))

    def cleanup(self) -> None:
        if self.keep_user or not self.user_id:
            return
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM orders WHERE user_id = %s", (self.user_id,))
                deleted_orders = cursor.rowcount
                cursor.execute("DELETE FROM family_members WHERE user_id = %s", (self.user_id,))
                deleted_family = cursor.rowcount
                cursor.execute("DELETE FROM user_tokens WHERE user_id = %s", (self.user_id,))
                deleted_tokens = cursor.rowcount
                cursor.execute("DELETE FROM users WHERE id = %s", (self.user_id,))
                deleted_users = cursor.rowcount
            conn.commit()
            self.add("cleanup_temp_user", deleted_users == 1, f"orders={deleted_orders}, family={deleted_family}, tokens={deleted_tokens}, users={deleted_users}")
        finally:
            conn.close()

    def write_log(self) -> None:
        passed = sum(1 for item in self.results if item.status == "PASS")
        failed = len(self.results) - passed
        lines = [
            "# System E2E Test Log",
            "",
            f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
            f"- base_url: {self.base_url}",
            f"- total_checks: {len(self.results)}",
            f"- passed: {passed}",
            f"- failed: {failed}",
            f"- llm_provider_events: `{json.dumps(self.provider_events, ensure_ascii=False)}`",
            "",
            "## Summary",
            "",
            "| check | status | detail |",
            "|---|---|---|",
        ]
        for item in self.results:
            detail = str(item.detail or "").replace("\n", " ")[:220]
            lines.append(f"| {item.name} | {item.status} | {detail or '-'} |")
        lines.extend(["", "## Failed Checks"])
        failures = [item for item in self.results if item.status != "PASS"]
        if not failures:
            lines.append("")
            lines.append("None.")
        for item in failures:
            lines.extend([
                "",
                f"### {item.name}",
                "",
                f"- detail: {item.detail}",
                f"- data: `{json.dumps(item.data, ensure_ascii=False)}`",
            ])
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run(self) -> int:
        try:
            self.check_static_and_compile()
            self.check_database()
            self.auth_and_user_flow()
            self.family_flow()
            self.public_and_map_flow()
            self.memory_flow()
            self.chat_and_llm_flow()
            self.replace_and_order_flow()
        finally:
            self.cleanup()
            self.write_log()
        failed = [item for item in self.results if item.status != "PASS"]
        print(json.dumps({
            "log": str(self.log_path),
            "total": len(self.results),
            "passed": len(self.results) - len(failed),
            "failed": len(failed),
            "failed_checks": [item.name for item in failed],
            "llm_provider_events": self.provider_events,
        }, ensure_ascii=False, indent=2))
        return 1 if failed else 0

    @staticmethod
    def _safe_path(path: str) -> str:
        split = urllib.parse.urlsplit(path)
        safe_path = urllib.parse.quote(split.path, safe="/")
        safe_query = urllib.parse.quote(split.query, safe="=&%")
        return urllib.parse.urlunsplit(("", "", safe_path, safe_query, split.fragment))

    @staticmethod
    def _extract_version(text: str) -> str:
        marker = '<div id="app-version" class="app-version">'
        if marker not in text:
            return ""
        return text.split(marker, 1)[1].split("</div>", 1)[0].strip()

    @staticmethod
    def _count_food(steps: list[dict[str, Any]]) -> int:
        words = ["餐", "饭", "咖啡", "茶", "火锅", "烤鸭", "酒吧", "小吃", "美食"]
        return sum(1 for step in steps if any(word in f"{step.get('name', '')} {step.get('category', '')} {step.get('meta', '')}" for word in words))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--keep-user", action="store_true")
    args = parser.parse_args()
    return E2ERunner(args.base_url, Path(args.log), keep_user=args.keep_user).run()


if __name__ == "__main__":
    raise SystemExit(main())
