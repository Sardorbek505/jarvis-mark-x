"""
Действие: погода через wttr.in (без API-ключа)
"""

import json
import time
import urllib.parse
import urllib.request


_CITY_ALIASES = {
    "москва": "Moscow",
    "питер": "Saint Petersburg",
    "санкт-петербург": "Saint Petersburg",
    "алматы": "Almaty",
    "алма-ата": "Almaty",
    "астана": "Astana",
    "нур-султан": "Astana",
    "кызылорда": "Kyzylorda",
    "шымкент": "Shymkent",
    "чимкент": "Shymkent",
    "ташкент": "Tashkent",
    "бишкек": "Bishkek",
    "минск": "Minsk",
    "киев": "Kyiv",
    "баку": "Baku",
    "тбилиси": "Tbilisi",
    "дубай": "Dubai",
}

_DESC_RU = {
    "Sunny": "Солнечно",
    "Clear": "Ясно",
    "Partly cloudy": "Переменная облачность",
    "Cloudy": "Облачно",
    "Overcast": "Пасмурно",
    "Mist": "Туман",
    "Fog": "Густой туман",
    "Light rain": "Лёгкий дождь",
    "Moderate rain": "Умеренный дождь",
    "Heavy rain": "Сильный дождь",
    "Light snow": "Лёгкий снег",
    "Moderate snow": "Умеренный снег",
    "Heavy snow": "Сильный снег",
    "Thundery outbreaks possible": "Возможна гроза",
    "Blizzard": "Метель",
    "Patchy rain possible": "Местами дождь",
}


# Коды погоды WWO (их отдаёт wttr.in) → значок карточки.
_KIND_BY_CODE = {
    113: "sun", 116: "partly",
    119: "cloud", 122: "cloud", 143: "fog", 248: "fog", 260: "fog",
    200: "storm", 386: "storm", 389: "storm", 392: "storm", 395: "storm",
}
_RAIN = {176, 263, 266, 281, 284, 293, 296, 299, 302, 305, 308, 311, 314, 353, 356, 359}
_SNOW = {179, 182, 185, 227, 230, 317, 320, 323, 326, 329, 332, 335, 338, 350,
         362, 365, 368, 371, 374, 377}
_DAY_NAMES = ("Сегодня", "Завтра", "Послезавтра")


def weather_kind(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "cloud"
    if code in _RAIN:
        return "rain"
    if code in _SNOW:
        return "snow"
    return _KIND_BY_CODE.get(code, "cloud")


# Последний прогноз по городу — для карточки рядом с шаром.
last_forecast: dict[str, dict] = {}

_WEATHER_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SEC = 300  # 5 минут


def weather_action(parameters: dict, player=None) -> str:
    city = parameters.get("city", "").strip()
    city_en = _CITY_ALIASES.get(city.lower(), city)
    now = time.monotonic()

    # Проверка TTL-кэша
    cache_key = city_en.lower()
    if cache_key in _WEATHER_CACHE:
        cached_time, cached_result = _WEATHER_CACHE[cache_key]
        if now - cached_time < _CACHE_TTL_SEC:
            if player:
                player.write_log(f"SYS: Погода (кэш) — {city}")
            return cached_result

    try:
        url = f"https://wttr.in/{urllib.parse.quote(city_en)}?format=j1"
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read().decode())

        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        feels = current["FeelsLikeC"]
        desc_en = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        wind = current["windspeedKmph"]

        desc_ru = _DESC_RU.get(desc_en, desc_en)

        # Прогноз на три дня. Раньше бралась только погода «сейчас» — на
        # «погода на завтра» Джарвис отвечал про сегодня.
        days = []
        for i, day in enumerate(data.get("weather", [])[:3]):
            hourly = day.get("hourly") or [{}]
            noon = hourly[min(4, len(hourly) - 1)]            # 12:00
            d_en = (noon.get("weatherDesc") or [{}])[0].get("value", "")
            rain = max((int(h.get("chanceofrain", 0) or 0) for h in hourly), default=0)
            days.append({
                "name": _DAY_NAMES[i],
                "max": day.get("maxtempC", "?"), "min": day.get("mintempC", "?"),
                "desc": _DESC_RU.get(d_en.strip(), d_en.strip()),
                "rain": rain, "kind": weather_kind(noon.get("weatherCode")),
            })

        result = (
            f"Погода в {city}: {desc_ru}. "
            f"Температура {temp_c}°C, ощущается как {feels}°C. "
            f"Влажность {humidity}%, ветер {wind} км/ч."
        )
        if days:
            parts = []
            for d in days:
                line = f"{d['name']}: {d['desc'].lower() or 'без описания'}, от {d['min']} до {d['max']}°C"
                if d["rain"] >= 30:
                    line += f", вероятность дождя {d['rain']}%"
                parts.append(line + ".")
            result += " Прогноз: " + " ".join(parts)

        last_forecast[city.lower()] = {
            "city": city, "temp": temp_c, "feels": feels, "desc": desc_ru,
            "humidity": humidity, "wind": wind,
            "kind": weather_kind(current.get("weatherCode")), "days": days,
        }

        _WEATHER_CACHE[cache_key] = (now, result)

        if player:
            player.write_log(f"SYS: Погода — {city}: {temp_c}°C, {desc_ru}")
        return result

    except Exception:
        return f"Не удалось получить погоду для {city}. Проверьте подключение к интернету."
