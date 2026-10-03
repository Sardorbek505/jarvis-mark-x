"""Где я: город, координаты, часовой пояс.

Источник — по порядку: домашний город из настроек («home_city» в
api_keys.json, его ставит «запомни, что я живу в …») и местоположение по
IP-адресу (с точностью до города; ipwho.is, запасной — ipapi.co). Город →
координаты и пояс — геокодер Open-Meteo (без ключа).

Нужно: «где я», «какой у меня город», время в другом городе (core/clock.py),
погода без названия города, «сколько до Ташкента».
"""
from __future__ import annotations

import json
import logging
import math
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_CACHE_SEC = 30 * 60
_cache: dict = {"at": 0.0, "place": None}
_geo_cache: dict[str, dict | None] = {}


def _get_json(url: str, timeout: float = 6.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Jarvis/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(city: str) -> dict | None:
    """Город → {name, country, lat, lon, timezone}. None — не нашёл."""
    key = (city or "").strip().lower()
    if not key:
        return None
    if key in _geo_cache:
        return _geo_cache[key]
    place = None
    try:
        d = _get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&language=ru&name="
                      + urllib.parse.quote(city.strip()))
        r = (d.get("results") or [None])[0]
        if r:
            place = {"name": r.get("name", city), "country": r.get("country", ""),
                     "lat": r.get("latitude"), "lon": r.get("longitude"), "timezone": r.get("timezone", "")}
    except Exception as exc:
        logger.debug("Геокодер «%s»: %s", city, exc)
        return None                              # сеть моргнула — не запоминаем промах
    _geo_cache[key] = place
    return place


def is_unknown_city(city: str) -> bool:
    """Геокодер ответил «такого нет» (а не сеть моргнула — сбой в кэш не пишется)."""
    key = (city or "").strip().lower()
    return key in _geo_cache and _geo_cache[key] is None


def _home_city() -> str:
    try:
        from core.paths import load_api_keys
        return str(load_api_keys().get("home_city") or "").strip()
    except Exception:
        return ""


def _by_ip() -> dict | None:
    for url, conv in (
        ("https://ipwho.is/?lang=ru", lambda d: d.get("success") is not False and {
            "name": d.get("city", ""), "region": d.get("region", ""), "country": d.get("country", ""),
            "lat": d.get("latitude"), "lon": d.get("longitude"),
            "timezone": (d.get("timezone") or {}).get("id", "")}),
        ("https://ipapi.co/json/", lambda d: not d.get("error") and {
            "name": d.get("city", ""), "region": d.get("region", ""), "country": d.get("country_name", ""),
            "lat": d.get("latitude"), "lon": d.get("longitude"), "timezone": d.get("timezone", "")}),
    ):
        try:
            place = conv(_get_json(url))
            if place and place.get("name"):
                place["source"] = "ip"
                return place
        except Exception as exc:
            logger.debug("Местоположение по IP (%s): %s", url, exc)
    return None


def where(refresh: bool = False) -> dict | None:
    """Где пользователь сейчас (кэш полчаса)."""
    if not refresh and _cache["place"] and time.time() - _cache["at"] < _CACHE_SEC:
        return _cache["place"]
    place = None
    home = _home_city()
    if home:
        place = geocode(home)
        if place:
            place = {**place, "source": "home"}
    if not place:
        place = _by_ip()
    if place:
        _cache.update(at=time.time(), place=place)
    return place


def city() -> str:
    p = where()
    return (p or {}).get("name", "")


def distance_km(a: dict, b: dict) -> float:
    """Расстояние по прямой (по дуге Земли), км."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def describe(place: dict | None) -> str:
    if not place:
        return "Не удалось определить, где вы: нет связи с интернетом. Скажите «я живу в …» — запомню."
    name, region, country = place.get("name", ""), place.get("region", ""), place.get("country", "")
    where_txt = ", ".join(x for x in (name, region if region != name else "", country) if x)
    src = {"home": "по вашим настройкам", "ip": "по интернет-подключению, точность — город"}.get(
        place.get("source", ""), "")
    return f"Вы в {where_txt}" + (f" ({src})" if src else "") + "."


def set_home(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Какой город запомнить, сэр?"
    place = geocode(name)
    if not place:
        return f"Не нашёл город «{name}»."
    from core.paths import load_api_keys, save_api_keys
    keys = load_api_keys()
    keys["home_city"] = place["name"]
    save_api_keys(keys)
    _cache.update(at=0.0, place=None)
    return f"Запомнил: ваш город — {place['name']}, {place['country']}."


def location_tool(p: dict) -> str:
    p = p or {}
    a = (p.get("action") or "where").strip().lower()
    if a == "where":
        return describe(where(refresh=bool(p.get("refresh"))))
    if a == "set_home":
        return set_home(str(p.get("city") or ""))
    if a == "distance":
        target = geocode(str(p.get("city") or ""))
        if not target:
            return f"Не нашёл город «{p.get('city', '')}»."
        src = geocode(str(p["from_city"])) if p.get("from_city") else where()
        if not src or src.get("lat") is None:
            return "Не знаю, откуда считать: не удалось определить, где вы."
        km = distance_km(src, target)
        return f"От {src['name']} до {target['name']} по прямой около {round(km):,} км.".replace(",", " ")
    if a == "coordinates":
        pl = geocode(str(p["city"])) if p.get("city") else where()
        if not pl:
            return "Не удалось определить координаты."
        return f"{pl['name']}: {pl['lat']:.4f}, {pl['lon']:.4f}, часовой пояс {pl.get('timezone') or 'неизвестен'}."
    return f"Не понял действие «{a}»."
