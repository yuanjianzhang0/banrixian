# -*- coding: utf-8 -*-
"""Local Agent smoke test. No server, database, or external LLM is required."""

from __future__ import annotations

import asyncio
import json
import sys

from agent.planner import run_agent, run_agent_stream
from place import PLACES

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


PLACE_FIELDS = (
    "name",
    "category",
    "keyword",
    "address",
    "open_hours",
    "price_range",
    "score",
    "desc_text",
    "lng",
    "lat",
)


def places_to_dicts(raw_places: list | tuple) -> list[dict]:
    return [dict(zip(PLACE_FIELDS, place)) for place in raw_places]


async def main() -> None:
    text = "今天下午2点，我想和老婆孩子还有两个朋友出去玩，别太远，孩子5岁，老婆最近在减肥，帮我安排一下，如果适合也帮我预约"
    places = places_to_dicts(PLACES)
    valid_names = {place["name"] for place in places}

    print("=== Agent stream events ===")
    async for event in run_agent_stream(
        text=text,
        current_user={"city": "北京", "tags": ["亲子", "周末"]},
        family_members=[],
        places=places,
    ):
        print(json.dumps(event, ensure_ascii=False, indent=2))

    print("\n=== Final plan only ===")
    plan = await run_agent(
        text=text,
        current_user={"city": "北京"},
        family_members=[],
        places=places,
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    assert plan["type"] == "plan"
    assert "亲子出行" in plan["profile"]["scene"]
    assert "伴侣同行" in plan["profile"]["scene"]
    assert "朋友聚会" in plan["profile"]["scene"]
    assert plan["profile"]["people"]["adults"] == 4
    assert plan["profile"]["people"]["children"] == 1
    assert any("下午2点" in item for item in plan["profile"]["constraints"])
    assert any("距离不要太远" in item for item in plan["profile"]["constraints"])
    assert any("饮食低负担" in item for item in plan["profile"]["constraints"])
    assert 2 <= len(plan["steps"]) <= 4
    assert all(step["name"] in valid_names for step in plan["steps"])
    assert any(action["type"] == "reserve" for action in plan["actions"])


if __name__ == "__main__":
    asyncio.run(main())
