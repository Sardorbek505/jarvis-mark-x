"""Маршрутизация фильмов: movie_player → оркестратор, парсер → провайдер, fast-path.

Раньше тесты здесь проверяли заглушку `browser_control`, которую movie_player
звал «для legacy-тестов», — настоящий запуск шёл мимо неё, и зелёные тесты
ничего не говорили о реальной работе. Теперь проверяется то, что происходит
на самом деле: кому movie_player отдаёт запрос и какой провайдер выбирает парсер.
"""

from unittest.mock import MagicMock, patch

import pytest

from actions.movie_player import movie_player
from core.fast_command_router import FastCommandRouter
from core.media.models import MediaType, parse_media_request


def test_movie_player_empty_title():
    res = movie_player({"action": "play", "title": ""})
    assert "Назовите фильм" in res


def test_movie_player_play_delegates_to_orchestrator_once():
    orch = MagicMock()
    orch.play_media.return_value = "Включаю «Гладиатор» на VK Видео, сэр."
    with patch("actions.movie_player.get_media_orchestrator", return_value=orch):
        res = movie_player({"action": "play", "platform": "vkvideo", "title": "Гладиатор"})

    orch.play_media.assert_called_once()
    title, params = orch.play_media.call_args.args
    assert title == "Гладиатор"
    assert params["platform"] == "vkvideo"
    assert res == "Включаю «Гладиатор» на VK Видео, сэр."


@pytest.mark.parametrize(
    "title,params,expected_provider",
    [
        ("Inception Trailer", {"platform": "youtube"}, "youtube"),
        ("Интерстеллар", {"platform": "kinopoisk"}, "kinopoisk"),
        ("Гладиатор", {"platform": "vkvideo"}, "vkvideo"),
        ("трейлер Аватар 3", {}, "youtube"),
        ("на ютубе клип Linkin Park", {}, "youtube"),
        ("на кинопоиске Дюна 2", {}, "kinopoisk"),
        ("Интерстеллар", {}, "vk"),
    ],
)
def test_parser_picks_provider(title, params, expected_provider):
    assert parse_media_request(title, params).provider == expected_provider


def test_explicit_music_type_is_not_turned_into_movie():
    """«Linkin Park» без слова «песня» — музыка, если так сказал вызывающий."""
    req = parse_media_request("Linkin Park", {"media_type": "music"})
    assert req.media_type == MediaType.MUSIC
    assert req.provider == "spotify"


def test_music_player_sends_music_query_as_string():
    """play_media ждёт строку; объект MediaRequest превращался в фильм."""
    from actions.music_player import music_player

    orch = MagicMock()
    orch.play_media.return_value = "Включаю Linkin Park, сэр."
    with patch("actions.music_player.get_media_orchestrator", return_value=orch):
        music_player({"action": "play", "query": "Linkin Park"})

    query, params = orch.play_media.call_args.args
    assert query == "Linkin Park"
    assert params["media_type"] == "music"


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
