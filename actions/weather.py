"""
Действие: погода через wttr.in (без API-ключа)
"""

import urllib.request
import urllib.parse
import json


_CITY_ALIASES = {
    "москва": "Moscow",
    "питер": "Saint Petersburg",
    "санкт-петербург": "Saint Petersburg",
    "алматы": "Almaty",
    "алма-ата": "Almaty",
    "астана": "Astana",
    "нур-султан": "Astana",
    "кызылорда": "Kyzylorda",
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


def weather_action(parameters: dict, player=None) -> str:
    city = parameters.get("city", "").strip()
    city_en = _CITY_ALIASES.get(city.lower(), city)

    try:
        url = f"https://wttr.in/{urllib.parse.quote(city_en)}?format=j1"
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read().decode())

        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        feels = current["FeelsLikeC"]
        desc_en = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]
        wind = current["windspeedKmph"]

        desc_ru = _DESC_RU.get(desc_en, desc_en)

        result = (
            f"Погода в {city}: {desc_ru}. "
            f"Температура {temp_c}°C, ощущается как {feels}°C. "
            f"Влажность {humidity}%, ветер {wind} км/ч."
        )

        if player:
            player.write_log(f"SYS: Погода — {city}: {temp_c}°C, {desc_ru}")
        return result

    except Exception as e:
        return f"Не удалось получить погоду для {city}. Проверьте подключение к интернету."
