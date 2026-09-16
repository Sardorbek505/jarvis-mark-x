"""Время, дата и погода отвечаются локально, без Gemini.

Стенд 16.09.2026: «который час» через модель — 4–7 с, «какая погода» — до
11 с (модель + инструмент). Локально это 0 мс + голос.
"""

import datetime as dt
from unittest.mock import patch

import pytest

from actions import local_answers as la
from actions import weather as w
from core.fast_command_router import FastCommandRouter


# ── время и дата ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("now,expected", [
    (dt.datetime(2026, 9, 16, 14, 5), "Сейчас 14 часов 5 минут, сэр."),
    (dt.datetime(2026, 9, 16, 1, 1), "Сейчас 1 час 1 минута, сэр."),
    (dt.datetime(2026, 9, 16, 22, 0), "Сейчас ровно 22 часа, сэр."),
    (dt.datetime(2026, 9, 16, 0, 30), "Сейчас 0 часов 30 минут, сэр."),
    (dt.datetime(2026, 9, 16, 3, 22), "Сейчас 3 часа 22 минуты, сэр."),
])
def test_time_phrase_declension(now, expected):
    assert la.time_answer(now) == expected


def test_date_phrase():
    assert la.date_answer(dt.datetime(2026, 9, 16)) == "Сегодня среда, 16 сентября 2026 года, сэр."


@pytest.mark.parametrize("text", [
    "Джарвис, который час?", "сколько времени", "который сейчас час",
    "Джарвис, скажи время", "сколько сейчас времени",
])
def test_router_answers_time_locally(text):
    with patch("actions.local_answers.time_answer", return_value="Сейчас 14 часов 5 минут, сэр."):
        res = FastCommandRouter.match_and_execute(text)
    assert res[0] is True
    assert res[1] == "Сейчас 14 часов 5 минут, сэр."
    assert res.is_action is False, "ответ должен быть озвучен, а не заменён щелчком"


@pytest.mark.parametrize("text", ["какое сегодня число", "Джарвис, какой сегодня день?", "какая сегодня дата"])
def test_router_answers_date_locally(text):
    res = FastCommandRouter.match_and_execute(text)
    assert res[0] is True and res[1].startswith("Сегодня ")


def test_router_leaves_time_related_questions_to_model():
    """«Сколько времени займёт дорога» — не про часы."""
    for text in ("сколько времени займёт дорога до Алматы", "который час в Нью-Йорке был вчера"):
        assert FastCommandRouter.match_and_execute(text)[0] is False


# ── погода ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spoken,expected", [
    ("Шымкенте", ["шымкенте", "шымкент", "шымкента"]),
    ("Москве", ["москве", "москв", "москва"]),
    ("Казани", ["казани", "казан", "казань", "казаня"]),
    ("Питере", ["санкт-петербург"]),
    ("Шемкинте", ["шымкент"]),
    ("Алматы", ["алматы"]),
])
def test_city_nominative_candidates(spoken, expected):
    got = [c.lower() for c in w.city_candidates(spoken)]
    assert got[: len(expected)] == expected or set(expected) <= set(got), got


def test_weather_speech_uses_spoken_form_of_city(monkeypatch):
    monkeypatch.setattr(w, "fetch_weather", lambda city: {
        "city": "Шымкент", "desc": "Ясно", "temp": 24, "feels": 20, "humidity": 26, "wind": 17,
    })
    text = la.weather_answer("Шымкенте")
    assert text == "В Шымкенте ясно, 24 градуса, ощущается как 20. Ветер 17 километров в час, сэр."


def test_weather_speech_degrees_declension(monkeypatch):
    base = {"city": "Москва", "desc": "Пасмурно", "humidity": 80, "wind": 3}
    monkeypatch.setattr(w, "fetch_weather", lambda city: {**base, "temp": 1, "feels": -2})
    assert "1 градус," in la.weather_answer("Москве") and "минус 2" in la.weather_answer("Москве")
    monkeypatch.setattr(w, "fetch_weather", lambda city: {**base, "temp": 22, "feels": 22})
    assert "22 градуса, ощущается как 22" in la.weather_answer("Москве")
    monkeypatch.setattr(w, "fetch_weather", lambda city: {**base, "temp": 15, "feels": 15})
    assert "15 градусов" in la.weather_answer("Москве")


def test_weather_default_city_when_not_named(monkeypatch):
    seen = {}

    def fake(city):
        seen["city"] = city
        return {"city": city, "desc": "Ясно", "temp": 20, "feels": 20, "humidity": 30, "wind": 5}

    monkeypatch.setattr(w, "fetch_weather", fake)
    monkeypatch.setattr(la, "default_city", lambda: "Шымкент")
    la.weather_answer("")
    assert seen["city"] == "Шымкент"


def test_weather_failure_is_honest(monkeypatch):
    monkeypatch.setattr(w, "fetch_weather", lambda city: None)
    assert la.weather_answer("Нарния") == "Не удалось получить погоду для Нарния, сэр."


@pytest.mark.parametrize("text,city", [
    ("Джарвис, какая сегодня погода в Шымкенте?", "Шымкенте"),
    ("какая погода", ""),
    ("погода в Москве", "Москве"),
    ("сколько сейчас градусов на улице", ""),
    ("что там с погодой в Ташкенте", "Ташкенте"),
    ("какая температура за окном", ""),
])
def test_router_answers_weather_locally(text, city):
    with patch("actions.local_answers.weather_answer", return_value="В X ясно, сэр.") as m:
        res = FastCommandRouter.match_and_execute(text)
    assert res[0] is True and res[1] == "В X ясно, сэр." and res.is_action is False
    assert m.call_args[0][0].lower() == city.lower()


def test_router_leaves_forecast_questions_to_model():
    for text in ("какая погода будет завтра", "будет ли дождь на выходных", "погода на неделю"):
        assert FastCommandRouter.match_and_execute(text)[0] is False


def test_geocoding_is_cached(monkeypatch):
    w._GEO_CACHE.clear()
    calls = []

    def fetch(url, params=None):
        calls.append(url)
        if url == w._GEO_URL:
            return {"results": [{"latitude": 42.3, "longitude": 69.6}]}
        return {"current": {"temperature_2m": 20, "apparent_temperature": 19, "relative_humidity_2m": 30,
                            "wind_speed_10m": 5, "weather_code": 0}}

    monkeypatch.setattr(w, "_fetch_json", fetch)
    w._WEATHER_CACHE.clear()
    w.fetch_weather("Шымкент")
    w._WEATHER_CACHE.clear()
    w.fetch_weather("Шымкент")
    assert calls.count(w._GEO_URL) == 1, "координаты города надо помнить"


def test_misheard_city_is_matched_fuzzily(monkeypatch):
    """«Шимкинте» (ослышка расшифровки) должен найтись как Шымкент."""
    w._GEO_CACHE.clear()
    asked = []

    def fetch(url, params=None):
        if url == w._GEO_URL:
            asked.append(params["name"])
            return {"results": [{"latitude": 42.3, "longitude": 69.6}]} if params["name"] == "Шымкент" else {"results": []}
        return {"current": {"temperature_2m": 20, "apparent_temperature": 19, "relative_humidity_2m": 30,
                            "wind_speed_10m": 5, "weather_code": 0}}

    monkeypatch.setattr(w, "_fetch_json", fetch)
    w._WEATHER_CACHE.clear()
    assert w.fetch_weather("Шимкинте") is not None
    assert "Шымкент" in asked
