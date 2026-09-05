"""Unit tests for Smart Universal Cinema & Media Routing and Stark Arc Reactor HUD."""

import pytest
from unittest.mock import MagicMock, patch
from core.fast_command_router import FastCommandRouter
from actions.movie_player import movie_player, _play


def test_movie_player_empty_title():
    res = movie_player({"action": "play", "title": ""})
    assert "Назовите фильм" in res


def test_movie_player_youtube_routing():
    browser_calls = []
    with patch("actions.movie_player.browser_control", lambda p, player=None: browser_calls.append(p)), \
         patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player._fullscreen", return_value="ok"), \
         patch("actions.movie_player.time.sleep", return_value=None):

        res = movie_player({"action": "play", "platform": "youtube", "title": "Inception Trailer"})
        assert "YouTube" in res
        assert len(browser_calls) == 1
        assert "youtube.com/results" in browser_calls[0]["url"]
        assert "Inception%20Trailer" in browser_calls[0]["url"]


def test_movie_player_kinopoisk_routing():
    browser_calls = []
    with patch("actions.movie_player.browser_control", lambda p, player=None: browser_calls.append(p)), \
         patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player.time.sleep", return_value=None):

        res = movie_player({"action": "play", "platform": "kinopoisk", "title": "Интерстеллар"})
        assert "Кинопоиске" in res
        assert len(browser_calls) == 1
        assert "kinopoisk.ru" in browser_calls[0]["url"]


def test_movie_player_vkvideo_routing():
    browser_calls = []
    with patch("actions.movie_player.browser_control", lambda p, player=None: browser_calls.append(p)), \
         patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player._fullscreen", return_value="ok"), \
         patch("actions.movie_player.time.sleep", return_value=None):

        res = movie_player({"action": "play", "platform": "vkvideo", "title": "Гладиатор"})
        assert "VK Видео" in res
        assert len(browser_calls) == 1
        assert "vkvideo.ru" in browser_calls[0]["url"]


def test_movie_player_auto_detection_trailer():
    browser_calls = []
    with patch("actions.movie_player.browser_control", lambda p, player=None: browser_calls.append(p)), \
         patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player._fullscreen", return_value="ok"), \
         patch("actions.movie_player.time.sleep", return_value=None):

        # Запрос трейлера без указания платформы должен автоматически уйти на YouTube
        res = movie_player({"action": "play", "title": "трейлер Аватар 3"})
        assert "YouTube" in res
        assert "youtube.com" in browser_calls[0]["url"]


def test_movie_player_auto_detection_in_title():
    browser_calls = []
    with patch("actions.movie_player.browser_control", lambda p, player=None: browser_calls.append(p)), \
         patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player._fullscreen", return_value="ok"), \
         patch("actions.movie_player.time.sleep", return_value=None):

        res = movie_player({"action": "play", "title": "на ютубе клип Linkin Park"})
        assert "YouTube" in res

        res_kp = movie_player({"action": "play", "title": "на кинопоиске Дюна 2"})
        assert "Кинопоиске" in res_kp


# ─── Fast-Path тесты ─────────────────────────────────────────────────────────

def test_fast_router_youtube():
    movie_calls = []
    with patch("actions.movie_player.movie_player", lambda p, player=None: (movie_calls.append(p), "ok")[1]), \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("Джарвис, включи на ютубе трейлер фильма Гладиатор 2")
        assert handled is True
        assert len(movie_calls) == 1
        assert movie_calls[0]["platform"] == "youtube"
        assert "трейлер фильма гладиатор 2" in movie_calls[0]["title"].lower()


def test_fast_router_trailer():
    movie_calls = []
    with patch("actions.movie_player.movie_player", lambda p, player=None: (movie_calls.append(p), "ok")[1]), \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("Джарвис, покажи трейлер фильма Матрица")
        assert handled is True
        assert len(movie_calls) == 1
        assert movie_calls[0]["platform"] == "youtube"
        assert "матрица" in movie_calls[0]["title"].lower()


def test_fast_router_kinopoisk():
    movie_calls = []
    with patch("actions.movie_player.movie_player", lambda p, player=None: (movie_calls.append(p), "ok")[1]), \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("найди на кинопоиске Оппенгеймер")
        assert handled is True
        assert len(movie_calls) == 1
        assert movie_calls[0]["platform"] == "kinopoisk"
        assert movie_calls[0]["title"] == "оппенгеймер"


def test_fast_router_movie_and_series():
    movie_calls = []
    with patch("actions.movie_player.movie_player", lambda p, player=None: (movie_calls.append(p), "ok")[1]), \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("включи фильм Интерстеллар")
        assert handled is True
        assert len(movie_calls) == 1
        assert movie_calls[0]["platform"] == "auto"
        assert movie_calls[0]["title"] == "интерстеллар"

        handled, resp = FastCommandRouter.match_and_execute("поставь сериал Чернобыль")
        assert handled is True
        assert len(movie_calls) == 2
        assert movie_calls[1]["platform"] == "auto"
        assert movie_calls[1]["title"] == "чернобыль"


def test_fast_router_compact_widget_mode():
    class DummyPlayer:
        def __init__(self):
            self.compact_mode = None
            self.logs = []

        def set_compact_mode(self, enabled: bool):
            self.compact_mode = enabled

        def write_log(self, msg: str):
            self.logs.append(msg)

    player = DummyPlayer()
    with patch("core.fast_command_router._trigger_action_feedback"):
        # Свернись в виджет
        handled, resp = FastCommandRouter.match_and_execute("Джарвис, свернись в виджет", player=player)
        assert handled is True
        assert player.compact_mode is True
        assert "компактный режим" in resp.lower()

        # Развернись
        handled, resp = FastCommandRouter.match_and_execute("Джарвис, разверни интерфейс", player=player)
        assert handled is True
        assert player.compact_mode is False
        assert "полный интерфейс" in resp.lower()
