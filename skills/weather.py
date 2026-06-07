# -*- coding: utf-8 -*-
"""Weather skill.

The skill never fabricates live weather. It either returns data from a real
provider or an explicit unavailable observation that the Agent can explain.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any


CITY_COORDS: dict[str, tuple[float, float]] = {
    "北京": (39.9042, 116.4074),
    "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644),
    "深圳": (22.5431, 114.0579),
    "杭州": (30.2741, 120.1551),
    "成都": (30.5728, 104.0668),
    "南京": (32.0603, 118.7969),
    "武汉": (30.5928, 114.3055),
    "西安": (34.3416, 108.9398),
    "重庆": (29.5630, 106.5516),
    "天津": (39.3434, 117.3616),
}


WEATHER_CODES = {
    0: "晴",
    1: "基本晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "阵雨",
    81: "较强阵雨",
    82: "强阵雨",
    95: "雷暴",
}

WEATHER_CACHE_TTL_SECONDS = 10 * 60
_WEATHER_CACHE: dict[str, tuple[float, dict]] = {}


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _resolve_city(city: str | None, current_user: dict | None = None) -> str:
    city = (city or "").strip()
    if city:
        return city
    current_user = current_user or {}
    return str(current_user.get("city") or "北京")


def _resolve_coords(city: str, lat: Any = None, lng: Any = None) -> tuple[float, float] | None:
    latitude = _as_float(lat)
    longitude = _as_float(lng)
    if latitude is not None and longitude is not None:
        return latitude, longitude
    return CITY_COORDS.get(city)


def _unavailable(city: str, reason: str) -> dict:
    return {
        "type": "weather",
        "status": "unavailable",
        "city": city,
        "source": "none",
        "message": f"实时天气暂不可用：{reason}",
        "advice": "不要编造气温、降雨、风力等实时天气信息；可基于用户偏好给出保守室内/室外备选。",
    }


def _cache_key(city: str, latitude: float, longitude: float) -> str:
    return f"{city}:{latitude:.3f}:{longitude:.3f}"


def _cached_weather(key: str) -> dict | None:
    cached = _WEATHER_CACHE.get(key)
    if not cached:
        return None
    saved_at, payload = cached
    age = time.time() - saved_at
    if age > WEATHER_CACHE_TTL_SECONDS:
        return None
    message = str(payload.get("message") or "")
    return {
        **payload,
        "source": "open-meteo-cache",
        "cached": True,
        "message": f"{message}（最近一次实时天气，约{max(1, int(age // 60) + 1)}分钟前更新）" if message else "已使用最近一次实时天气数据",
    }


def _exception_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _weather_timeout_seconds() -> float:
    raw = os.getenv("WEATHER_TIMEOUT_SECONDS", "3").strip()
    try:
        return max(0.8, min(5.0, float(raw)))
    except ValueError:
        return 3.0


async def get_weather(
    city: str | None = None,
    date: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    current_user: dict | None = None,
) -> dict:
    """Fetch current weather from a real provider or return unavailable."""
    city = _resolve_city(city, current_user=current_user)
    provider = os.getenv("WEATHER_PROVIDER", "open-meteo").strip().lower()
    if provider in {"disabled", "none", "off", "local"}:
        return _unavailable(city, "服务器未启用天气服务")
    if provider not in {"open-meteo", "openmeteo"}:
        return _unavailable(city, f"未知天气服务 provider={provider}")

    coords = _resolve_coords(city, lat=lat, lng=lng)
    if not coords:
        return _unavailable(city, "暂不支持该城市坐标解析")

    latitude, longitude = coords
    cache_key = _cache_key(city, latitude, longitude)
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_weather_timeout_seconds(), trust_env=False) as client:
            response = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,apparent_temperature,precipitation,rain,weather_code,wind_speed_10m",
                    "timezone": "Asia/Shanghai",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        cached = _cached_weather(cache_key)
        if cached:
            return cached
        return _unavailable(city, f"天气服务调用失败：{_exception_text(exc)}")

    current = payload.get("current") if isinstance(payload, dict) else None
    if not isinstance(current, dict):
        return _unavailable(city, "天气服务返回格式异常")

    weather_code = current.get("weather_code")
    weather_text = WEATHER_CODES.get(weather_code, "未知")
    observed_at = current.get("time") or datetime.now().strftime("%Y-%m-%d %H:%M")
    temperature = current.get("temperature_2m")
    apparent_temperature = current.get("apparent_temperature")
    precipitation = current.get("precipitation")
    rain = current.get("rain")
    wind_speed = current.get("wind_speed_10m")

    outdoor_risk: list[str] = []
    if isinstance(precipitation, (int, float)) and precipitation > 0:
        outdoor_risk.append("有降水")
    if isinstance(rain, (int, float)) and rain > 0:
        outdoor_risk.append("有雨")
    if isinstance(wind_speed, (int, float)) and wind_speed >= 25:
        outdoor_risk.append("风较大")

    result = {
        "type": "weather",
        "status": "ok",
        "city": city,
        "source": "open-meteo",
        "date": date or "today",
        "observed_at": observed_at,
        "weather": weather_text,
        "temperature_c": temperature,
        "apparent_temperature_c": apparent_temperature,
        "precipitation_mm": precipitation,
        "rain_mm": rain,
        "wind_speed_kmh": wind_speed,
        "outdoor_risk": outdoor_risk,
        "message": (
            f"{city}当前天气：{weather_text}，气温{temperature}℃"
            if temperature is not None
            else f"{city}当前天气：{weather_text}"
        ),
    }
    _WEATHER_CACHE[cache_key] = (time.time(), result)
    return result
