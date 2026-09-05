"""Unit tests for Smart Routines Engine and Fast-Path execution."""

import pytest
from unittest.mock import patch, MagicMock
from core.routines_engine import RoutinesEngine
from core.fast_command_router import FastCommandRouter


class MockPlayer:
    def __init__(self):
        self.logs = []
        self.compact_mode = False

    def write_log(self, msg: str):
        self.logs.append(msg)

    def set_compact_mode(self, enabled: bool):
        self.compact_mode = enabled


def test_routine_morning_execution():
    player = MockPlayer()
    with patch("actions.weather.weather_action", return_value="В Ташкенте сейчас +24°C, солнечно."), \
         patch("actions.calendar.get_todays_schedule", return_value="Встреча в 14:00"), \
         patch("actions.music_player.music_player", return_value="Включаю музыку"):

        res = RoutinesEngine.execute("morning", player=player)
        assert "Доброе утро" in res
        assert "Ташкенте" in res
        assert "Встреча в 14:00" in res
        assert any("Доброе утро" in l for l in player.logs)


def test_routine_work_execution():
    player = MockPlayer()
    with patch("actions.modes.set_mode", return_value="Режим работы активирован"), \
         patch("actions.music_player.music_player", return_value="Включаю lofi"):

        res = RoutinesEngine.execute("work", player=player)
        assert "Рабочий режим активирован" in res
        assert any("Я за работу" in l for l in player.logs)


def test_routine_movie_execution():
    player = MockPlayer()
    res = RoutinesEngine.execute("movie", player=player)
    assert "Режим кинотеатра активирован" in res
    assert player.compact_mode is True
    assert any("Режим кинотеатра" in l for l in player.logs)


def test_routine_bedtime_execution():
    player = MockPlayer()
    with patch("actions.music_player._send_media_key") as mock_media, \
         patch("actions.computer_settings.computer_settings") as mock_settings, \
         patch("actions.sleep_timer.sleep_timer") as mock_sleep:

        res = RoutinesEngine.execute("bedtime", player=player)
        assert "Доброй ночи" in res
        mock_media.assert_called_once_with("playpause")
        mock_settings.assert_called_once_with({"action": "заблокировать экран"}, player=player)
        mock_sleep.assert_called_once_with({"action": "set", "duration_minutes": 30}, player=player)


def test_routine_custom_macro_execution():
    player = MockPlayer()
    with patch("actions.music_player.music_player") as mock_music, \
         patch("actions.computer_settings.computer_settings") as mock_settings:

        res = RoutinesEngine.execute("relax", player=player)
        assert "Приятного отдыха" in res or "успешно выполнен" in res
        mock_music.assert_called_once()
        mock_settings.assert_called_once()


# ─── Fast-Path тесты для сценариев ───────────────────────────────────────────

def test_fast_router_morning():
    player = MockPlayer()
    with patch("core.routines_engine.RoutinesEngine.execute", return_value="Доброе утро, сэр!"), \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("Джарвис, доброе утро", player=player)
        assert handled is True
        assert "Доброе утро" in resp


def test_fast_router_work():
    player = MockPlayer()
    with patch("core.routines_engine.RoutinesEngine.execute", return_value="Рабочий режим активирован"), \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("я за работу", player=player)
        assert handled is True
        assert "Рабочий режим" in resp


def test_fast_router_movie():
    player = MockPlayer()
    with patch("core.routines_engine.RoutinesEngine.execute", return_value="Режим кинотеатра активирован"), \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("режим кинотеатра", player=player)
        assert handled is True
        assert "Режим кинотеатра" in resp


def test_fast_router_bedtime():
    player = MockPlayer()
    with patch("core.routines_engine.RoutinesEngine.execute", return_value="Доброй ночи, сэр"), \
         patch("core.fast_command_router._trigger_action_feedback"):

        handled, resp = FastCommandRouter.match_and_execute("спокойной ночи", player=player)
        assert handled is True
        assert "Доброй ночи" in resp
