"""Поиск, фильмы и браузер: настоящие результаты и честные ответы."""
import actions.browser_control as bc
import actions.movie_player as mv
from actions.web_search import web_search
from core import media_session, web_find
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


def test_film_opens_direct_vk_page(monkeypatch):
    opened = []
    monkeypatch.setattr(web_find, "search", lambda q, limit=8: [
        Hit("Интерстеллар", "https://vkvideo.ru/video-12345_67890", "")] if "vkvideo" in q else [])
    monkeypatch.setattr(mv, "browser_control", lambda p, player=None: opened.append(p["url"]) or "ok")
    monkeypatch.setattr(media_session, "wait_for", lambda *a, **k: None)
    out = mv.movie_player({"action": "play", "title": "Интерстеллар"})
    assert opened == ["https://vkvideo.ru/video-12345_67890"]
    assert "VK Видео" in out and "приятного" not in out


def test_film_falls_to_kinopoisk_then_search(monkeypatch):
    opened = []
    monkeypatch.setattr(mv, "browser_control", lambda p, player=None: opened.append(p["url"]) or "ok")
    monkeypatch.setattr(media_session, "wait_for", lambda *a, **k: None)
    monkeypatch.setattr(web_find, "search", lambda q, limit=8: [
        Hit("Дюна", "https://hd.kinopoisk.ru/film/409424", "")] if "kinopoisk" in q else [])
    assert "Кинопоиске" in mv.movie_player({"action": "play", "title": "Дюна"})
    monkeypatch.setattr(web_find, "search", lambda q, limit=8: [])
    out = mv.movie_player({"action": "play", "title": "Редкий фильм"})
    assert opened[-1].startswith("https://vkvideo.ru/?q=") and "не нашёл" in out


def test_movie_pause_uses_media_session(monkeypatch):
    sent = []
    monkeypatch.setattr(media_session, "command", lambda cmd, app=None: sent.append(cmd) or True)
    assert mv.movie_player({"action": "pause"}) == "Пауза."
    assert mv.movie_player({"action": "resume"}) == "Продолжаю."
    assert sent == ["pause", "play"]


def test_browser_reports_failure_honestly(monkeypatch):
    monkeypatch.setattr(bc, "_open_url", lambda url, browser=None: False)
    assert bc.browser_control({"action": "go_to", "url": "youtube.com"}).startswith("Не получилось")
    monkeypatch.setattr(bc, "_open_url", lambda url, browser=None: True)
    assert bc.browser_control({"action": "go_to", "url": "youtube.com"}) == "Открыл https://youtube.com."


def test_unknown_browser_name_is_not_executed():
    assert bc._browser_exe("powershell") is None
