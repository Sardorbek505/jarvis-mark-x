"""Spotify: обновление токена, ранжирование треков, приоритет песни над плейлистом.

Три бага, найденные по жалобе «иногда ставит музыку неправильно»:
  1. expiry=None в файле токенов → обновление не происходило НИКОГДА, наружу
     уходил протухший токен → 401 → «Spotify недоступен».
  2. search_track калечил запрос 20 «вариантами опечаток» (з→с, к→х, п→б),
     сваливал результаты в общий котёл, игнорировал popularity и давал +20 за
     точное совпадение названия — из-за чего кавер «Song» побеждал оригинал
     «Song - Remastered 2011».
  3. play_query включал первый попавшийся ГЛОБАЛЬНЫЙ плейлист раньше, чем
     вообще пробовал искать трек.
"""
import time

import pytest

from tools.spotify.auth import SpotifyAuth
from tools.spotify.search import SpotifySearch


# ─── 1. Обновление токена ─────────────────────────────────────────────────────
@pytest.fixture
def auth(tmp_path, monkeypatch):
    """SpotifyAuth без обращения к диску и сети."""
    monkeypatch.setattr(SpotifyAuth, "_load_tokens", lambda self: None)
    monkeypatch.setattr(SpotifyAuth, "_save_tokens", lambda self: None)
    a = SpotifyAuth("cid", "secret", "http://localhost/callback")
    a.refreshed = 0

    def fake_refresh():
        a.refreshed += 1
        a.access_token = "свежий"
        a.token_expiry = time.time() + 3600
        return True

    a.refresh_access_token = fake_refresh
    return a


def test_missing_expiry_forces_refresh(auth):
    """Главный баг: expiry=None в файле означал «токен вечен»."""
    auth.access_token = "протухший"
    auth.refresh_token = "refresh"
    auth.token_expiry = None

    token = auth.get_access_token()

    assert auth.refreshed == 1
    assert token == "свежий"


def test_expired_token_refreshes(auth):
    auth.access_token = "протухший"
    auth.refresh_token = "refresh"
    auth.token_expiry = time.time() - 1

    assert auth.get_access_token() == "свежий"
    assert auth.refreshed == 1


def test_valid_token_is_not_refreshed(auth):
    auth.access_token = "живой"
    auth.refresh_token = "refresh"
    auth.token_expiry = time.time() + 3600

    assert auth.get_access_token() == "живой"
    assert auth.refreshed == 0


def test_no_refresh_token_means_no_token(auth):
    auth.access_token = None
    auth.refresh_token = None

    assert auth.get_access_token() is None


# ─── 2. Ранжирование треков ───────────────────────────────────────────────────
def _track(name, artist):
    """Трек ровно в той форме, в какой его отдаёт /search.

    Поля popularity здесь НЕТ намеренно: живой ответ Spotify его не содержит,
    а /tracks отдаёт 403 для наших ключей. Первая версия этих тестов выдумывала
    popularity — и фикс «прошёл» тесты, но на реальном API включал кавер.
    """
    return {
        "id": f"{name}-{artist}",
        "name": name,
        "uri": f"spotify:track:{name}",
        "artists": [{"name": artist}],
        "explicit": False,
    }


@pytest.fixture
def search(monkeypatch):
    monkeypatch.setattr(SpotifySearch, "_api_request", lambda self, *a, **k: None)
    return SpotifySearch("token")


def test_original_beats_cover(search):
    """Живые данные: на «Bohemian Rhapsody» Spotify отдаёт Queen первым, кавер —
    третьим. Самодельная пересортировка поднимала кавер наверх; теперь её нет."""
    items = [
        _track("Bohemian Rhapsody", "Queen"),                     # позиция 0
        _track("Bohemian Rhapsody / Radio Ga Ga - Live", "Queen"),
        _track("Bohemian Rhapsody", "Angelina Jordan"),           # кавер
    ]
    search._api_request = lambda path, params=None: {"tracks": {"items": items}}

    best = search.search_track("Bohemian Rhapsody")

    assert best["artists"][0]["name"] == "Queen"


def test_named_artist_is_respected(search):
    """Живые данные: «Miyagi love me» — Spotify ставит MiyaGi первым, хотя
    название трека на латиницу не похоже. Пересортировка выбирала Lil Wayne."""
    items = [
        _track("Родной", "MiyaGi & Endspiel"),   # позиция 0, название не совпадает
        _track("Love Me", "Lil Wayne"),          # название совпадает точнее
    ]
    search._api_request = lambda path, params=None: {"tracks": {"items": items}}

    best = search.search_track("Miyagi love me")

    assert best["artists"][0]["name"] == "MiyaGi & Endspiel"


def test_no_results_returns_none(search):
    search._api_request = lambda path, params=None: {"tracks": {"items": []}}
    assert search.search_track("абырвалг несуществующий") is None


def test_query_is_not_mangled_into_typos(search):
    """Замены з→с, к→х калечили запрос и засоряли выдачу мусором."""
    calls = []

    def spy(path, params=None):
        calls.append(params.get("q") if params else None)
        return None

    search._api_request = spy
    search.search_track("Кино Группа крови")

    assert len(calls) <= 3, f"слишком много запросов к API: {len(calls)} -> {calls}"
    for q in calls:
        assert "х" not in q.lower() or "к" in "Кино".lower()
    assert "Кино Группа крови" in calls


# ─── 3. Песня важнее чужого плейлиста ─────────────────────────────────────────
@pytest.fixture
def controller(monkeypatch):
    from tools.spotify.controller import SpotifyController

    c = SpotifyController.__new__(SpotifyController)
    c.track_cache = {}
    c.last_query = c.last_uri = None
    c.played = []
    c._refresh_components = lambda: True
    c._cache_track = lambda q, u: None
    c.play_track = lambda uri: (c.played.append(("track", uri)), True)[1]
    c.play_context = lambda uri: (c.played.append(("playlist", uri)), True)[1]

    class FakeSearch:
        def get_user_playlists(self, limit=50):
            return []

        def search_playlists(self, query, limit=5):
            # Глобальный поиск почти всегда что-то находит — в этом и была беда
            return [{"name": f"{query} Radio", "uri": "spotify:playlist:чужой"}]

        def search_track(self, query):
            return {"uri": "spotify:track:нужный", "name": query}

    c.search = FakeSearch()
    return c


def test_song_wins_over_someone_elses_playlist(controller):
    """Главный баг: на просьбу поставить песню включался чужой плейлист."""
    controller.play_query("Bohemian Rhapsody")

    assert controller.played == [("track", "spotify:track:нужный")], controller.played


def test_playlist_is_the_fallback_when_no_track(controller):
    controller.search.search_track = lambda query: None

    controller.play_query("что-нибудь для тренировки")

    assert controller.played == [("playlist", "spotify:playlist:чужой")]


def test_own_playlist_still_wins_when_named_alike(controller):
    """«Включи мою Классику» должно открывать плейлист, а не искать трек."""
    controller.search.get_user_playlists = lambda limit=50: [
        {"name": "Классика", "uri": "spotify:playlist:моя"}
    ]

    controller.play_query("классику")

    assert controller.played == [("playlist", "spotify:playlist:моя")]
