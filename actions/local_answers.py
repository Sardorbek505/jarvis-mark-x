"""Ответы, которым не нужна модель: время, дата, текущая погода.

Через Gemini «который час» стоил 4–7 с, «какая погода» — до 11 с (модель +
инструмент + модель). Здесь ответ собирается за 0 мс, остаётся только голос.
Фразы короткие и однотипные — тем лучше для кэша голоса.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from actions import weather as _weather

_logger = logging.getLogger(__name__)

_BASE = Path(__file__).resolve().parent.parent

_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 час / 2 часа / 5 часов — по правилам русского языка."""
    n = abs(int(n))
    if 11 <= n % 100 <= 19:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def time_answer(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    hours = f"{now.hour} {plural(now.hour, 'час', 'часа', 'часов')}"
    if now.minute == 0:
        return f"Сейчас ровно {hours}, сэр."
    minutes = f"{now.minute} {plural(now.minute, 'минута', 'минуты', 'минут')}"
    return f"Сейчас {hours} {minutes}, сэр."


def date_answer(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    return f"Сегодня {_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS_GEN[now.month - 1]} {now.year} года, сэр."


def default_city() -> str:
    """Город по умолчанию: профиль пользователя, затем config/api_keys.json."""
    try:
        from core.user_profile import UserProfile
        city = (UserProfile(_BASE).profile.get("identity") or {}).get("city")
        if city:
            return str(city)
    except Exception as exc:
        _logger.debug("Город из профиля не прочитан: %s", exc)
    try:
        from core.paths import get_config_path
        cfg_path = get_config_path("api_keys.json")
    except Exception:
        cfg_path = _BASE / "config" / "api_keys.json"
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return str(json.load(f).get("default_city") or "")
    except Exception as exc:
        _logger.debug("default_city не прочитан: %s", exc)
        return ""


def _degrees(value: int) -> str:
    sign = "минус " if value < 0 else ""
    return f"{sign}{abs(value)} {plural(value, 'градус', 'градуса', 'градусов')}"


def _in_city(spoken: str) -> str:
    """«в Шымкенте» как сказал пользователь; именительный — с предлогом «в»."""
    spoken = spoken.strip()
    low = spoken.lower()
    if low.endswith(("е", "и", "у", "ю")) and not low.endswith("ы"):
        return f"В {spoken}"
    return f"В городе {spoken}"


def weather_answer(spoken_city: str) -> str:
    spoken_city = (spoken_city or "").strip()
    city = spoken_city or default_city()
    info = _weather.fetch_weather(city)
    if info is None:
        target = spoken_city or city or "вашего региона"
        return f"Не удалось получить погоду для {target}, сэр."

    where = _in_city(spoken_city) if spoken_city else (_in_city(city) if city else "За окном")
    desc = info["desc"].lower()
    feels_value = info["feels"]
    feels = f", ощущается как {'минус ' if feels_value < 0 else ''}{abs(feels_value)}"
    return (
        f"{where} {desc}, {_degrees(info['temp'])}{feels}. "
        f"Ветер {info['wind']} {plural(info['wind'], 'километр', 'километра', 'километров')} в час, сэр."
    )
