"""Поиск, фильмы и браузер: настоящие результаты и честные ответы."""
import actions.browser_control as bc
from actions.web_search import web_search
from core import web_find
from core.web_find import Hit


def test_search_returns_results_as_text(monkeypatch):
    monkeypatch.setattr(web_find, "search", lambda q, limit=6: [
        Hit("Курс доллара", "https://a", "92,5 рубля на сегодня"),
        Hit("ЦБ РФ", "https://b", "официальный курс"),
    ])
    out = web_search({"query": "курс доллара"})
    assert out == "По запросу «курс доллара»: Курс доллара — 92,5 рубля на сегодня | ЦБ РФ — официальный курс"


def test_search_nothing_found_does_not_open_browser(monkeypatch):
    monkeypatch.setattr(web_find, "search", lambda q, limit=6: [])
    monkeypatch.setattr(bc, "_open_url", lambda *a: (_ for _ in ()).throw(AssertionError("открыл браузер")))
    assert "ничего не дал" in web_search({"query": "x"})


def test_spotify_uri_from_search(monkeypatch):
    monkeypatch.setattr(web_find, "search", lambda q, limit=8: [
        Hit("Believer", "https://open.spotify.com/intl-ru/track/0pqnGHJpmpxLKifKRmU6WP", "")])
    assert web_find.spotify_uri("Believer", "track") == "spotify:track:0pqnGHJpmpxLKifKRmU6WP"


def test_browser_reports_failure_honestly(monkeypatch):
    monkeypatch.setattr(bc, "_open_url", lambda url, browser=None: False)
    assert bc.browser_control({"action": "go_to", "url": "youtube.com"}).startswith("Не получилось")
    monkeypatch.setattr(bc, "_open_url", lambda url, browser=None: True)
    assert bc.browser_control({"action": "go_to", "url": "youtube.com"}) == "Открыл https://youtube.com."


def test_unknown_browser_name_is_not_executed():
    assert bc._browser_exe("powershell") is None
