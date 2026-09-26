"""Погода: прогноз на три дня в ответе и данные для карточки."""
import io
import json

from actions import weather
from core.result_card import build_card


def _fake_wttr():
    def day(date, hi, lo, code, rain):
        return {"date": date, "maxtempC": hi, "mintempC": lo,
                "hourly": [{"weatherCode": code, "chanceofrain": rain,
                            "weatherDesc": [{"value": "Light rain" if rain else "Sunny"}]}] * 8}
    return {
        "current_condition": [{"temp_C": "22", "FeelsLikeC": "21", "humidity": "40",
                               "windspeedKmph": "11", "weatherCode": "113",
                               "weatherDesc": [{"value": "Sunny"}]}],
        "weather": [day("2026-09-25", "24", "12", "113", "0"),
                    day("2026-09-26", "17", "9", "296", "80"),
                    day("2026-09-27", "20", "10", "116", "10")],
    }


def _patch(monkeypatch):
    body = json.dumps(_fake_wttr()).encode()

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(weather.urllib.request, "urlopen", lambda *a, **k: _Resp(body))
    weather._WEATHER_CACHE.clear()


def test_answer_includes_tomorrow(monkeypatch):
    _patch(monkeypatch)
    text = weather.weather_action({"city": "Шымкент"})
    assert "Завтра: лёгкий дождь, от 9 до 17°C, вероятность дождя 80%" in text
    assert "Температура 22°C" in text


def test_card_gets_structured_forecast(monkeypatch):
    _patch(monkeypatch)
    result = weather.weather_action({"city": "Шымкент"})
    card = build_card("weather", {"city": "Шымкент"}, {"result": result})
    data = json.loads(card["extra"])
    assert data["card"] == "weather" and data["temp"] == "22" and data["kind"] == "sun"
    assert [d["kind"] for d in data["days"]] == ["sun", "rain", "partly"]
    assert card["address"] == "погода: Шымкент"


def test_weather_codes_map_to_icons():
    assert weather.weather_kind(113) == "sun"
    assert weather.weather_kind("338") == "snow"
    assert weather.weather_kind(389) == "storm"
    assert weather.weather_kind(None) == "cloud"
