"""
Действие: погода через Open-Meteo (без API-ключа).

wttr.in, который стоял здесь раньше, перестал отвечать (15.09.2026, http=000),
поэтому десктоп переведён на тот же провайдер, что и telegram_bot/weather.py:
  • geocoding-api.open-meteo.com  (город → координаты)
  • api.open-meteo.com            (текущая погода)
Город не указан → определяем по IP через ip-api.com.
"""

import json
import logging
import time
import urllib.parse
import urllib.request

_logger = logging.getLogger(__name__)

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_IP_LOCATION_URL = "http://ip-api.com/json/?lang=ru&fields=status,city,lat,lon"
_HTTP_TIMEOUT_SEC = 6

# Разговорные названия → то, что точно найдёт геокодер
_CITY_ALIASES = {
    "питер": "Санкт-Петербург",
    "алма-ата": "Алматы",
    "нур-султан": "Астана",
    "чимкент": "Шымкент",
    "шемкент": "Шымкент",
}

# WMO weather code → описание по-русски
_WMO_RU = {
    0: "Ясно", 1: "Малооблачно", 2: "Переменная облачность", 3: "Пасмурно",
    45: "Туман", 48: "Изморозь",
    51: "Морось", 53: "Морось", 55: "Сильная морось",
    56: "Ледяная морось", 57: "Ледяная морось",
    61: "Лёгкий дождь", 63: "Умеренный дождь", 65: "Сильный дождь",
    66: "Ледяной дождь", 67: "Ледяной дождь",
    71: "Лёгкий снег", 73: "Умеренный снег", 75: "Сильный снег", 77: "Снежная крупа",
    80: "Ливень", 81: "Ливень", 82: "Сильный ливень",
    85: "Снегопад", 86: "Сильный снегопад",
    95: "Гроза", 96: "Гроза с градом", 99: "Сильная гроза с градом",
}

_WEATHER_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SEC = 300  # 5 минут


def _fetch_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode())


def _locate_city(city: str) -> tuple[float, float, str]:
    """Координаты и отображаемое имя: по названию города или по IP, если город пуст."""
    if city:
        data = _fetch_json(_GEO_URL, {"name": city, "count": 1, "language": "ru"})
        results = data.get("results") or []
        if not results:
            raise LookupError(f"город не найден: {city}")
        hit = results[0]
        return float(hit["latitude"]), float(hit["longitude"]), city

    data = _fetch_json(_IP_LOCATION_URL)
    if data.get("status") != "success":
        raise LookupError("не удалось определить регион по IP")
    return float(data["lat"]), float(data["lon"]), data.get("city") or "вашем регионе"


def _current_weather(lat: float, lon: float) -> dict:
    data = _fetch_json(_FORECAST_URL, {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
        "timezone": "auto",
    })
    return data["current"]


def weather_action(parameters: dict, player=None) -> str:
    city = parameters.get("city", "").strip()
    city_query = _CITY_ALIASES.get(city.lower(), city)
    now = time.monotonic()

    cache_key = city_query.lower() if city_query else "__default__"
    if cache_key in _WEATHER_CACHE:
        cached_time, cached_result = _WEATHER_CACHE[cache_key]
        if now - cached_time < _CACHE_TTL_SEC:
            if player:
                player.write_log(f"SYS: Погода (кэш) — {city or 'текущая'}")
            return cached_result

    try:
        lat, lon, display_city = _locate_city(city_query)
        if city:
            display_city = city
        current = _current_weather(lat, lon)

        temp_c = round(current["temperature_2m"])
        feels = round(current["apparent_temperature"])
        humidity = round(current["relative_humidity_2m"])
        wind = round(current["wind_speed_10m"])
        desc_ru = _WMO_RU.get(current.get("weather_code"), "Без осадков")

        result = (
            f"Погода ({display_city}): {desc_ru}. "
            f"Температура {temp_c}°C, ощущается как {feels}°C. "
            f"Влажность {humidity}%, ветер {wind} км/ч."
        )
        _WEATHER_CACHE[cache_key] = (now, result)

        if player:
            player.write_log(f"SYS: Погода — {display_city}: {temp_c}°C, {desc_ru}")
        return result

    except Exception as exc:
        _logger.warning("Погода для '%s' недоступна: %s", city or "текущего региона", exc)
        target = city if city else "вашего региона"
        return f"Не удалось получить погоду для {target}. Проверьте подключение к интернету."
