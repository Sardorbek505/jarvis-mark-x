import unittest
from unittest.mock import patch

from actions.weather import weather_action, _WEATHER_CACHE, _GEO_CACHE, _GEO_URL, _FORECAST_URL, _IP_LOCATION_URL


_CURRENT = {
    "temperature_2m": 18.4,
    "apparent_temperature": 17.2,
    "relative_humidity_2m": 45,
    "wind_speed_10m": 12.3,
    "weather_code": 0,
}


def _fake_fetch(city_hits: list, ip_status: str = "success"):
    def fetch(url, params=None):
        if url == _GEO_URL:
            return {"results": city_hits}
        if url == _FORECAST_URL:
            return {"current": _CURRENT}
        if url == _IP_LOCATION_URL:
            return {"status": ip_status, "city": "Almaty", "lat": 43.2, "lon": 76.9}
        raise AssertionError(f"неожиданный URL {url}")
    return fetch


class TestWeatherFixes(unittest.TestCase):
    def setUp(self):
        _WEATHER_CACHE.clear()
        _GEO_CACHE.clear()

    def test_empty_city_uses_detected_location(self):
        with patch("actions.weather._fetch_json", side_effect=_fake_fetch([])):
            res = weather_action({"city": ""})
        self.assertNotIn("Погода в :", res)
        self.assertIn("Погода (Almaty):", res)
        self.assertIn("Ясно", res)
        self.assertIn("18°C", res)
        self.assertIn("ощущается как 17°C", res)

    def test_explicit_city_formatting(self):
        hits = [{"latitude": 55.75, "longitude": 37.62}]
        with patch("actions.weather._fetch_json", side_effect=_fake_fetch(hits)):
            res = weather_action({"city": "Москва"})
        self.assertIn("Погода (Москва):", res)
        self.assertIn("Ясно", res)
        self.assertIn("Влажность 45%, ветер 12 км/ч", res)

    def test_alias_is_sent_to_geocoder_but_user_wording_is_shown(self):
        seen = {}

        def fetch(url, params=None):
            if url == _GEO_URL:
                seen["name"] = params["name"]
                return {"results": [{"latitude": 42.3, "longitude": 69.6}]}
            return {"current": _CURRENT}

        with patch("actions.weather._fetch_json", side_effect=fetch):
            res = weather_action({"city": "Чимкент"})
        self.assertEqual(seen["name"], "Шымкент")
        self.assertIn("Погода (Чимкент):", res)

    def test_unknown_city_error_fallback(self):
        with patch("actions.weather._fetch_json", side_effect=_fake_fetch([])):
            res = weather_action({"city": "Нарния"})
        self.assertIn("для Нарния", res)

    def test_empty_city_error_fallback(self):
        with patch("actions.weather._fetch_json", side_effect=Exception("Network error")):
            res = weather_action({"city": ""})
        self.assertIn("для вашего региона", res)
        self.assertNotIn("для .", res)

    def test_result_is_cached(self):
        hits = [{"latitude": 55.75, "longitude": 37.62}]
        with patch("actions.weather._fetch_json", side_effect=_fake_fetch(hits)) as m:
            weather_action({"city": "Москва"})
            weather_action({"city": "Москва"})
        self.assertEqual(m.call_count, 2)  # геокодер + прогноз, второй вызов из кэша


if __name__ == "__main__":
    unittest.main()
