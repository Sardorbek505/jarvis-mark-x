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
from typing import Optional

_logger = logging.getLogger(__name__)

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_IP_LOCATION_URL = "http://ip-api.com/json/?lang=ru&fields=status,city,lat,lon"
_HTTP_TIMEOUT_SEC = 6

# Разговорные названия и ослышки расшифровки → то, что точно найдёт геокодер
_CITY_ALIASES = {
    "питер": "Санкт-Петербург",
    "алма-ата": "Алматы",
    "алмата": "Алматы",
    "нур-султан": "Астана",
    "чимкент": "Шымкент",
    "шемкент": "Шымкент",
    "шемкинт": "Шымкент",
    "шимкент": "Шымкент",
    "шимкинт": "Шымкент",
    "шымкинт": "Шымкент",
    "чимкинт": "Шымкент",
}
# Известные города для нечёткого сопоставления ослышек расшифровки
_KNOWN_CITIES = (
    "Шымкент", "Алматы", "Астана", "Ташкент", "Бишкек", "Москва", "Санкт-Петербург",
    "Казань", "Туркестан", "Тараз", "Кызылорда", "Караганда", "Самарканд", "Бухара",
    "Душанбе", "Баку", "Тбилиси", "Минск", "Киев", "Дубай", "Стамбул",
)

# Координаты по городу не меняются — геокодер спрашиваем один раз за процесс.
_GEO_CACHE: dict[str, tuple[float, float]] = {}


def city_candidates(spoken: str) -> list[str]:
    """Варианты именительного падежа для сказанного «в Шымкенте», «в Москве».

    Геокодер Open-Meteo понимает только именительный: «Шымкенте» → пусто.
    Сначала псевдонимы (они же ловят ослышки), потом снятие окончаний
    предложного падежа: -е → «», -а; -и → «», -ь, -я. Порядок = порядок проб.
    """
    raw = (spoken or "").strip()
    if not raw:
        return []
    out: list[str] = []

    def add(c: str) -> None:
        c = c.strip()
        if c and c.lower() not in (x.lower() for x in out):
            out.append(c)

    alias = _CITY_ALIASES.get(raw.lower())
    if alias:
        return [alias]
    add(raw)
    low = raw.lower()
    if low.endswith("е"):
        add(raw[:-1])
        add(raw[:-1] + "а")
    elif low.endswith("и"):
        add(raw[:-1])
        add(raw[:-1] + "ь")
        add(raw[:-1] + "я")
    elif low.endswith(("у", "ю")):
        add(raw[:-1] + "а")
    # Псевдоним может совпасть уже со снятым окончанием («шемкинте» → «шемкинт»)
    for c in list(out):
        alias = _CITY_ALIASES.get(c.lower())
        if alias:
            out.remove(c)
            if alias.lower() not in (x.lower() for x in out):
                out.insert(0, alias)
    return out


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

_WEATHER_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SEC = 300  # 5 минут


def _fetch_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode())


def _geocode(city: str) -> Optional[tuple[float, float]]:
    key = city.lower()
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    data = _fetch_json(_GEO_URL, {"name": city, "count": 1, "language": "ru"})
    results = data.get("results") or []
    if not results:
        return None
    hit = results[0]
    _GEO_CACHE[key] = (float(hit["latitude"]), float(hit["longitude"]))
    return _GEO_CACHE[key]


def _locate_city(city: str) -> tuple[float, float, str]:
    """Координаты и отображаемое имя: по названию города или по IP, если город пуст."""
    if city:
        candidates = city_candidates(city)
        # Ослышка расшифровки («Шимкинте») — нечётко к известному городу
        import difflib
        for candidate in list(candidates):
            close = difflib.get_close_matches(candidate.lower(), [c.lower() for c in _KNOWN_CITIES], n=1, cutoff=0.75)
            if close:
                known = next(c for c in _KNOWN_CITIES if c.lower() == close[0])
                if known.lower() not in (x.lower() for x in candidates):
                    candidates.append(known)
        for candidate in candidates:
            coords = _geocode(candidate)
            if coords:
                return coords[0], coords[1], city
        raise LookupError(f"город не найден: {city}")

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


def fetch_weather(city: str) -> Optional[dict]:
    """Текущая погода в виде словаря (city, desc, temp, feels, humidity, wind) или None.

    Общий источник и для ответа Gemini, и для локального голосового ответа.
    Кэш 5 минут на город.
    """
    city = (city or "").strip()
    cache_key = city.lower() if city else "__default__"
    now = time.monotonic()
    cached = _WEATHER_CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return cached[1]
    try:
        lat, lon, display_city = _locate_city(city)
        current = _current_weather(lat, lon)
    except Exception as exc:
        _logger.warning("Погода для '%s' недоступна: %s", city or "текущего региона", exc)
        return None
    info = {
        "city": display_city,
        "desc": _WMO_RU.get(current.get("weather_code"), "Без осадков"),
        "temp": round(current["temperature_2m"]),
        "feels": round(current["apparent_temperature"]),
        "humidity": round(current["relative_humidity_2m"]),
        "wind": round(current["wind_speed_10m"]),
    }
    _WEATHER_CACHE[cache_key] = (now, info)
    return info


def weather_action(parameters: dict, player=None) -> str:
    city = parameters.get("city", "").strip()
    info = fetch_weather(city)
    if info is None:
        target = city if city else "вашего региона"
        return f"Не удалось получить погоду для {target}. Проверьте подключение к интернету."

    result = (
        f"Погода ({info['city']}): {info['desc']}. "
        f"Температура {info['temp']}°C, ощущается как {info['feels']}°C. "
        f"Влажность {info['humidity']}%, ветер {info['wind']} км/ч."
    )
    if player:
        player.write_log(f"SYS: Погода — {info['city']}: {info['temp']}°C, {info['desc']}")
    return result
