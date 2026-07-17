"""Per-user live context — current local time/date and location.

The Render server runs in UTC and has no idea where the user is. The phone
(Mini App) reports its own timezone + city, so JARVIS always knows the correct
local time and where the user currently is — wherever they travel.
"""
from datetime import datetime, timezone, timedelta
from typing import Dict

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

_WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]
_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

# user_id -> {"tz": str, "city": str, "lat": float, "lon": float}
_store: Dict[int, dict] = {}


def update(user_id: int, *, tz: str = None, city: str = None,
           lat: float = None, lon: float = None):
    """Store whatever the client reported (only non-empty fields)."""
    info = _store.setdefault(user_id, {})
    if tz:
        info["tz"] = tz
    if city:
        info["city"] = city
    if lat is not None and lon is not None:
        info["lat"] = lat
        info["lon"] = lon


def get_city(user_id: int, default_city: str = "") -> str:
    return _store.get(user_id, {}).get("city") or default_city


def _resolve_tz(name: str):
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    # Fallback: UTC+5 (Shymkent / Kazakhstan)
    return timezone(timedelta(hours=5))


def local_now(user_id: int, default_tz: str = "Asia/Almaty") -> datetime:
    tz_name = _store.get(user_id, {}).get("tz") or default_tz
    return datetime.now(_resolve_tz(tz_name))


def describe(user_id: int, default_city: str = "", default_tz: str = "Asia/Almaty") -> str:
    """One-line context string injected into every Gemini request."""
    now = local_now(user_id, default_tz)
    weekday = _WEEKDAYS[now.weekday()]
    date_str = f"{weekday}, {now.day} {_MONTHS[now.month - 1]} {now.year} г."
    time_str = now.strftime("%H:%M")
    city = get_city(user_id, default_city)

    parts = [
        f"Сейчас {date_str}, время {time_str}.",
    ]
    if city:
        parts.append(f"Пользователь сейчас находится в городе {city}.")
    parts.append(
        "Используй ЭТИ данные о времени и месте. Никогда не выдумывай другой город "
        "(например, не говори про Москву, если пользователь не в Москве) и не путай дату."
    )
    return " ".join(parts)
