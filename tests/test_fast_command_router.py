"""Тесты для быстрого локального роутера FastCommandRouter."""

import pytest
from core.fast_command_router import FastCommandRouter, normalize_command_text


class MockPlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg: str):
        self.logs.append(msg)


def test_normalize_command_text():
    assert normalize_command_text("Джарвис, пауза!") == "пауза"
    assert normalize_command_text("Эй Джарвис сделай потише...") == "сделай потише"
    assert normalize_command_text("Джервис, перемотай вперед на 15 секунд?") == "перемотай вперед на 15 секунд"
    assert normalize_command_text("Какая сегодня погода?") == "какая сегодня погода"


def test_fast_router_media_pause(monkeypatch):
    called = []
    monkeypatch.setattr("actions.music_player._send_media_key", lambda action: called.append(action))

    player = MockPlayer()
    handled, resp = FastCommandRouter.match_and_execute("Джарвис, пауза!", player=player)
    assert handled is True
    assert "паузу" in resp
    assert called == ["playpause"]
    assert any("Пауза" in log for log in player.logs)


def test_fast_router_media_resume(monkeypatch):
    called = []
    monkeypatch.setattr("actions.music_player._send_media_key", lambda action: called.append(action))

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, играй")
    assert handled is True
    assert "воспроизведение" in resp
    assert called == ["playpause"]


def test_fast_router_media_next(monkeypatch):
    called = []
    monkeypatch.setattr("actions.music_player._send_media_key", lambda action: called.append(action))

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, следующий трек")
    assert handled is True
    assert "следующий трек" in resp
    assert called == ["next"]


def test_fast_router_volume(monkeypatch):
    settings_calls = []
    monkeypatch.setattr("actions.computer_settings.computer_settings", lambda params, player=None: settings_calls.append(params))

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, сделай потише")
    assert handled is True
    assert "тише" in resp
    assert settings_calls[0]["description"] == "тише"

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, сделай погромче")
    assert handled is True
    assert "громче" in resp
    assert settings_calls[1]["description"] == "громче"


def test_fast_router_fullscreen(monkeypatch):
    movie_calls = []
    monkeypatch.setattr("actions.movie_player.movie_player", lambda params, player=None: movie_calls.append(params))

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, на весь экран")
    assert handled is True
    assert "полный экран" in resp
    assert movie_calls[0]["action"] == "fullscreen"


def test_fast_router_seek(monkeypatch):
    movie_calls = []
    monkeypatch.setattr("actions.movie_player.movie_player", lambda params, player=None: movie_calls.append(params))

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, перемотай вперед на 20 секунд")
    assert handled is True
    assert "вперёд на 20 сек" in resp
    assert movie_calls[0]["action"] == "seek_forward"
    assert movie_calls[0]["seconds"] == 20

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, перемотай назад на 2 минуты")
    assert handled is True
    assert "назад на 2 мин" in resp
    assert movie_calls[1]["action"] == "seek_back"
    assert movie_calls[1]["minutes"] == 2


def test_fast_router_non_command():
    # Фразы, которые должны уйти в LLM и НЕ перехватываться Fast-Path
    handled, resp = FastCommandRouter.match_and_execute("Джарвис, какая сегодня погода?")
    assert handled is False
    assert resp is None

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, включи песню Numb Linkin Park")
    assert handled is False
    assert resp is None

    handled, resp = FastCommandRouter.match_and_execute("Джарвис, кто такой Илон Маск?")
    assert handled is False
    assert resp is None
