"""Музыка: включается нужный трек, ответ — то, что реально заиграло.

Раньше «включи X» жал Space и Play/Pause подряд (два переключателя гасили
друг друга), «пауза» на уже стоящей музыке её запускала, а ответ был
«Включаю…» при любом исходе. Здесь медиа-сессия Spotify подменена фейком.
"""
import pytest

import actions.music_player as mp
from core import media_session
from core.media_session import NowPlaying


class _Spotify:
    """Фейк медиа-сессии: что играет и какие команды пришли."""

    def __init__(self, title=None, playing=False, opens_playing=True, opens="Believer"):
        self.np = NowPlaying("Spotify.exe", title, "Imagine Dragons", playing) if title else None
        self.cmds = []
        self.opens_playing, self.opens = opens_playing, opens

    def now_playing(self, app=None):
        return self.np

    def command(self, cmd, app=None):
        self.cmds.append(cmd)
        if self.np is None:
            return False
        self.np.playing = cmd in ("play",) or (cmd == "toggle" and not self.np.playing)
        if cmd in ("next", "previous"):
            self.np.title = "Thunder"
        return True

    def wait_for(self, app, timeout=8.0, playing=None):
        return self.np if self.np and (playing is None or self.np.playing == playing) else None

    def open_uri(self, uri):
        self.np = NowPlaying("Spotify.exe", self.opens, "Imagine Dragons", self.opens_playing)
        return True


@pytest.fixture(autouse=True)
def _no_premium_api(monkeypatch):
    """Путь Premium Web API — выключен: здесь проверяется медиа-сессия.

    Без этого тесты шли в живой Spotify на машине разработчика (ready() там
    правдив) и, например, «next» честно переключал человеку трек, а ответ
    приходил из API — мимо всего, что проверяется ниже.
    """
    from actions import spotify_premium
    monkeypatch.setattr(spotify_premium, "ready", lambda: False)


@pytest.fixture
def spotify(monkeypatch):
    def make(**kw):
        fake = _Spotify(**kw)
        for name in ("now_playing", "command", "wait_for"):
            monkeypatch.setattr(media_session, name, getattr(fake, name))
        monkeypatch.setattr(mp, "_open_spotify_uri", fake.open_uri)
        monkeypatch.setattr(mp, "_is_spotify_installed", lambda: True)
        monkeypatch.setattr(mp.time, "sleep", lambda s: None)
        return fake
    return make


def test_play_track_reports_what_actually_plays(spotify, monkeypatch):
    fake = spotify(title="Old Song", playing=True)
    monkeypatch.setattr(mp, "_spotify_search_track_uri", lambda q: "spotify:track:abc")
    assert mp._play(query="Believer") == "Включил «Believer» — Imagine Dragons."
    assert fake.cmds == []                       # без лишних переключателей


def test_track_opened_paused_gets_one_play(spotify, monkeypatch):
    fake = spotify(opens_playing=False)
    monkeypatch.setattr(mp, "_spotify_search_track_uri", lambda q: "spotify:track:abc")
    answer = mp._play(query="Believer")
    assert fake.cmds == ["play"] and answer.startswith("Включил")


def test_no_exact_track_opens_spotify_search_honestly(spotify, monkeypatch):
    spotify()
    monkeypatch.setattr(mp, "_spotify_search_track_uri", lambda q: None)
    from core import web_find
    monkeypatch.setattr(web_find, "spotify_uri", lambda q, kind=None: None)
    opened = []
    monkeypatch.setattr(mp, "_open_spotify_uri", lambda uri: opened.append(uri) or True)
    answer = mp._play(query="редкая песня")
    assert opened[0].startswith("spotify:search:") and "открыл поиск" in answer


def test_pause_is_explicit_not_toggle(spotify):
    fake = spotify(title="Believer", playing=False)          # уже на паузе
    assert mp.music_player({"action": "pause"}) == "Пауза."
    assert fake.cmds == ["pause"] and fake.np.playing is False   # не запустилась


def test_next_names_the_new_track(spotify):
    spotify(title="Believer", playing=True)
    assert mp.music_player({"action": "next"}) == "Следующий: «Thunder» — Imagine Dragons."


def test_nothing_playing(spotify):
    spotify()
    assert mp.music_player({"action": "pause"}) == "Сейчас ничего не играет, сэр."
    assert mp.music_player({"action": "now_playing"}) == "Сейчас ничего не играет, сэр."


def test_youtube_when_spotify_missing(monkeypatch):
    monkeypatch.setattr(mp, "_is_spotify_installed", lambda: False)
    monkeypatch.setattr(mp, "_find_youtube_direct_url", lambda q: "https://youtu.be/x")
    monkeypatch.setattr(mp, "browser_control", lambda *a, **k: "ok")
    assert "YouTube" in mp._play(query="Smells Like Teen Spirit")

    def boom(*a, **k):
        raise RuntimeError("browser is gone")
    monkeypatch.setattr(mp, "browser_control", boom)
    assert mp._play(query="x").startswith("Не удалось")
