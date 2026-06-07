# -*- coding: utf-8 -*-
"""Provider-agnostic LLM client for the planning agent."""

from __future__ import annotations

import json
import os

from core.config import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    HUAWEI_MAAS_API_KEY,
    HUAWEI_MAAS_BASE_URL,
    HUAWEI_MAAS_MODEL,
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
    LLM_PROVIDER,
)
from core.errors import LLMUnavailableError, LLMTimeoutError, LLMError


# Backward compatibility alias
LLMUnavailable = LLMUnavailableError


_DOTENV_LOADED = False


def _strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv_if_present() -> None:
    """Load a simple .env file without adding a python-dotenv dependency."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    current = os.path.abspath(os.getcwd())
    candidates: list[str] = []
    while True:
        candidates.append(os.path.join(current, ".env"))
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidates.append(os.path.join(project_root, ".env"))

    env_path = next((path for path in candidates if os.path.exists(path)), None)
    if not env_path:
        return

    try:
        with open(env_path, "r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = _strip_env_value(value)
    except OSError:
        return




class BaseLLMClient:
    provider_name = "unknown"
    model_name = ""

    async def complete(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError


def _is_fast_plan_decision(messages: list[dict[str, str]]) -> bool:
    payload = "\n".join(str(message.get("content") or "") for message in messages[-3:])
    return "路线裁判" in payload and "selected_index" in payload


def _is_fast_intent_request(messages: list[dict[str, str]]) -> bool:
    payload = "\n".join(str(message.get("content") or "") for message in messages[-3:])
    return "需求理解器" in payload and "keywords" in payload


def _llm_max_tokens_for(messages: list[dict[str, str]]) -> int:
    if _is_fast_plan_decision(messages) or _is_fast_intent_request(messages):
        raw = os.getenv("LLM_FAST_MAX_TOKENS", "96").strip()
        try:
            return max(32, min(160, int(raw)))
        except ValueError:
            return 96
    return min(int(LLM_MAX_TOKENS or 512), 512)


def _llm_http_timeout_for(messages: list[dict[str, str]]) -> float:
    if _is_fast_plan_decision(messages) or _is_fast_intent_request(messages):
        raw = os.getenv("LLM_FAST_HTTP_TIMEOUT_SECONDS", "8.5").strip()
        try:
            return max(1.0, min(9.0, float(raw)))
        except ValueError:
            return 8.5
    return 90.0


def _observed_tools(messages: list[dict[str, str]]) -> set[str]:
    tools: set[str] = set()
    for message in messages:
        content = message.get("content", "")
        if "OBSERVATION_JSON:" not in content:
            continue
        raw = content.split("OBSERVATION_JSON:", 1)[1].strip()
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        tool_name = payload.get("tool_name")
        if tool_name:
            tools.add(tool_name)
    return tools


def _last_observation(messages: list[dict[str, str]], tool_name: str) -> dict:
    for message in reversed(messages):
        content = message.get("content", "")
        if "OBSERVATION_JSON:" not in content:
            continue
        raw = content.split("OBSERVATION_JSON:", 1)[1].strip()
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if payload.get("tool_name") == tool_name:
            result = payload.get("result")
            return result if isinstance(result, dict) else {}
    return {}


def _last_user_request(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        content = message.get("content", "")
        if "USER_REQUEST:" not in content:
            continue
        request = content.split("USER_REQUEST:", 1)[1]
        if "\n\n" in request:
            request = request.split("\n\n", 1)[0]
        return request.strip()
    return ""


def _needs_weather(text: str) -> bool:
    weather_words = [
        "天气", "下雨", "有雨", "雨天", "晒", "冷不冷", "热不热", "温度", "气温",
        "风大", "户外", "室外", "适合出门", "适合去户外",
    ]
    return any(word in text for word in weather_words)


class LocalFallbackLLMClient(BaseLLMClient):
    """Deterministic local decision maker used when no API key is configured."""

    provider_name = "local"
    model_name = "fallback"

    async def complete(self, messages: list[dict[str, str]]) -> str:
        observed = _observed_tools(messages)
        if "analyze_user_profile" not in observed:
            return json.dumps({
                "type": "tool_call",
                "tool": "analyze_user_profile",
                "arguments": {},
                "visible_reason": "先分析同行人、时间、距离和饮食约束",
            }, ensure_ascii=False)

        if "get_weather" not in observed and _needs_weather(_last_user_request(messages)):
            profile = _last_observation(messages, "analyze_user_profile")
            return json.dumps({
                "type": "tool_call",
                "tool": "get_weather",
                "arguments": {"city": profile.get("city") or "北京", "date": "today"},
                "visible_reason": "用户提到天气或户外适宜性，需要查询真实天气，避免编造实时天气",
            }, ensure_ascii=False)

        if "search_places" not in observed:
            profile = _last_observation(messages, "analyze_user_profile")
            keyword = "亲子" if "亲子出行" in profile.get("scene", []) else "休闲"
            return json.dumps({
                "type": "tool_call",
                "tool": "search_places",
                "arguments": {"keyword": keyword, "limit": 8},
                "visible_reason": "根据画像从真实地点库检索候选地点",
            }, ensure_ascii=False)

        if "rank_places_for_plan" not in observed:
            return json.dumps({
                "type": "tool_call",
                "tool": "rank_places_for_plan",
                "arguments": {"limit": 8},
                "visible_reason": "对 search_places 返回的真实候选地点排序",
            }, ensure_ascii=False)

        if "build_final_plan" not in observed:
            return json.dumps({
                "type": "tool_call",
                "tool": "build_final_plan",
                "arguments": {},
                "visible_reason": "整理为前端可直接展示的结构化方案",
            }, ensure_ascii=False)

        final_plan = _last_observation(messages, "build_final_plan")
        return json.dumps({"type": "final_answer", "data": final_plan}, ensure_ascii=False)


class OpenAIClient(BaseLLMClient):
    def __init__(self) -> None:
        self.provider_name = "openai"
        self.api_key = OPENAI_API_KEY
        self.base_url = OPENAI_BASE_URL
        self.model = OPENAI_MODEL
        self.model_name = self.model
        if not self.api_key:
            raise LLMUnavailableError("OPENAI_API_KEY is not configured")

    async def complete(self, messages: list[dict[str, str]]) -> str:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=_llm_http_timeout_for(messages), trust_env=False) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": LLM_TEMPERATURE,
                        "max_tokens": _llm_max_tokens_for(messages),
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"OpenAI request timeout: {e}")
        except Exception as e:
            raise LLMError(f"OpenAI API error: {e}")


class HuaweiClient(BaseLLMClient):
    def __init__(self) -> None:
        self.provider_name = "huawei"
        self.api_key = HUAWEI_MAAS_API_KEY
        self.base_url = HUAWEI_MAAS_BASE_URL
        self.model = HUAWEI_MAAS_MODEL
        self.model_name = self.model
        if not self.api_key:
            raise LLMUnavailableError("HUAWEI_MAAS_API_KEY is not configured")

    async def complete(self, messages: list[dict[str, str]]) -> str:
        import asyncio
        import httpx

        is_fast_decision = _is_fast_plan_decision(messages) or _is_fast_intent_request(messages)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=_llm_http_timeout_for(messages), trust_env=False) as client:
                    response = await client.post(
                        f"{self.base_url.rstrip('/')}/v2/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json={
                            "model": self.model,
                            "messages": messages,
                            "temperature": LLM_TEMPERATURE,
                            "max_tokens": _llm_max_tokens_for(messages),
                            "stream": False,
                            "chat_template_kwargs": {"enable_thinking": False},
                        },
                    )
                # 429 限流：等待后重试
                if response.status_code == 429:
                    wait = 2 ** attempt  # 1s / 2s / 4s
                    if attempt < max_retries - 1:
                        await asyncio.sleep(wait)
                        continue
                    raise LLMError("Huawei API rate limited (429), please retry later")
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except httpx.TimeoutException as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                raise LLMTimeoutError(f"Huawei request timeout: {e}")
            except LLMError:
                raise
            except Exception as e:
                raise LLMError(f"Huawei API error: {e}")


def resolve_provider(provider: str | None = None) -> str:
    """Choose a real provider when credentials exist; otherwise use local fallback."""
    load_dotenv_if_present()
    provider = (provider or LLM_PROVIDER or "auto").lower()
    if provider != "auto":
        return provider
    if OPENAI_API_KEY:
        return "openai"
    if HUAWEI_MAAS_API_KEY:
        return "huawei"
    return "local"


def create_llm_client(provider: str | None = None) -> BaseLLMClient:
    load_dotenv_if_present()
    provider = resolve_provider(provider)
    try:
        if provider == "openai":
            return OpenAIClient()
        if provider == "huawei":
            return HuaweiClient()
        if provider in {"local", "fallback", "mock"}:
            return LocalFallbackLLMClient()
        raise LLMUnavailableError(f"Unsupported LLM_PROVIDER: {provider}")
    except LLMUnavailableError:
        raise
    except Exception as e:
        raise LLMError(f"Failed to initialize LLM client: {e}")
