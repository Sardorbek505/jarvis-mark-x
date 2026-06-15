"""
Weather for the morning briefing — free Open-Meteo API (no key required).

Network note: the deploy must allow egress to these hosts:
  • geocoding-api.open-meteo.com   (city → coordinates)
  • api.open-meteo.com             (forecast)
If they're blocked or anything fails, for_city() returns None and the briefing
simply continues without weather — never crashes.
"""
import logging

logger = logging.getLogger("jarvis-weather")

_GEO = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes → short Russian description + emoji
_CODES = {
    0: "☀️ ясно", 1: "🌤 малооблачно", 2: "⛅️ переменная облачность",
    3: "☁️ пасмурно", 45: "🌫 туман", 48: "🌫 изморозь",
    51: "🌦 морось", 53: "🌦 морось", 55: "🌦 сильная морось",
    61: "🌧 небольшой дождь", 63: "🌧 дождь", 65: "🌧 сильный дождь",
    71: "🌨 небольшой снег", 73: "🌨 снег", 75: "❄️ сильный снег",
    80: "🌦 ливень", 81: "🌦 ливень", 82: "⛈ сильный ливень",
    95: "⛈ гроза", 96: "⛈ гроза с градом", 99: "⛈ сильная гроза",
}


async def for_city(city: str) -> str | None:
    """Return a short weather line for the city, or None on any failure."""
    city = (city or "").strip()
    if not city:
        return None
    try:
        import httpx
    except Exception:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as cl:
            g = await cl.get(_GEO, params={"name": city, "count": 1, "language": "ru"})
            results = (g.json() or {}).get("results") or []
            if not results:
                return None
            lat, lon = results[0]["latitude"], results[0]["longitude"]
            f = await cl.get(_FORECAST, params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto", "forecast_days": 1,
            })
            data = f.json() or {}
            cur = data.get("current", {})
            daily = data.get("daily", {})
            temp = round(cur.get("temperature_2m"))
            desc = _CODES.get(cur.get("weather_code"), "")
            tmax = round(daily.get("temperature_2m_max", [temp])[0])
            tmin = round(daily.get("temperature_2m_min", [temp])[0])
            return f"{desc}, сейчас {temp}°, днём от {tmin}° до {tmax}°"
    except Exception as e:
        logger.debug(f"weather for '{city}': {e}")
        return None
