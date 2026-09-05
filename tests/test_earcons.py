"""JARVIS Mark X — Тесты звукового дизайна и звуковых сигналов (Earcons)."""

import numpy as np
import pytest
from core.earcons import (
    _synthesize_earcon,
    play_earcon,
    play_activation_chime,
    play_success_earcon,
    play_error_earcon,
    play_mute_earcon,
)
from core.fast_command_router import FastCommandRouter, FastCommandResult


def test_earcon_synthesis():
    """Проверка процедурной генерации всех типов звуковых сигналов."""
    for kind in ("wake", "success", "error", "mute", "unmute"):
        buf = _synthesize_earcon(kind)
        assert isinstance(buf, np.ndarray), f"Earcon {kind} должен быть numpy ndarray"
        assert buf.dtype == np.float32, f"Earcon {kind} должен иметь dtype float32"
        assert len(buf) > 0, f"Earcon {kind} не должен быть пустым"
        # Проверяем отсутствие клиппинга амплитуды
        assert np.max(np.abs(buf)) <= 1.0, f"Earcon {kind} превышает диапазон [-1.0, 1.0]"


def test_earcon_durations():
    """Проверка длительности ультракоротких щелчков (success <= 50ms)."""
    success_buf = _synthesize_earcon("success", sr=24000)
    dur_sec = len(success_buf) / 24000
    assert dur_sec <= 0.05, f"Success earcon должен быть ультракоротким (до 50 мс), получено {dur_sec} с"


def test_play_earcon_mocked(monkeypatch):
    """Проверка вызова воспроизведения без реального вывода на звуковую карту."""
    played = []
    monkeypatch.setattr("sounddevice.play", lambda data, sr: played.append((len(data), sr)))
    monkeypatch.setattr("sounddevice.wait", lambda: None)

    play_earcon("success", async_play=False)
    assert len(played) == 1
    assert played[0][1] == 24000

    play_activation_chime()
    play_success_earcon()
    play_error_earcon()
    play_mute_earcon(is_muted=True)
    play_mute_earcon(is_muted=False)


def test_fast_command_result_tuple_compatibility():
    """Проверка обратной совместимости FastCommandResult с кортежем (handled, text)."""
    res = FastCommandResult(True, "Тестовый ответ", is_action=True)
    # Распаковка как обычный 2-tuple
    handled, text = res
    assert handled is True
    assert text == "Тестовый ответ"
    # Доступ к расширенным атрибутам
    assert res.is_action is True
    assert res.handled is True
    assert res.text == "Тестовый ответ"


def test_fast_router_action_flags(monkeypatch):
    """Проверка: физические действия помечаются is_action=True, а информационные — is_action=False."""
    monkeypatch.setattr("actions.music_player._send_media_key", lambda action: None)
    monkeypatch.setattr("actions.computer_settings.computer_settings", lambda p, player=None: "ok")
    monkeypatch.setattr("actions.movie_player.movie_player", lambda p, player=None: "ok")
    monkeypatch.setattr("core.media_session_manager.MediaSessionManager.get_now_playing_speech", lambda: "Играет песня")

    earcons_played = []
    monkeypatch.setattr("core.earcons.play_success_earcon", lambda: earcons_played.append("success"))

    # 1. Пауза — физическое действие
    res_pause = FastCommandRouter.match_and_execute("Джарвис, пауза")
    assert res_pause.handled is True
    assert res_pause.is_action is True
    assert "success" in earcons_played

    # 2. Громкость — физическое действие
    res_vol = FastCommandRouter.match_and_execute("тише")
    assert res_vol.handled is True
    assert res_vol.is_action is True

    # 3. Полный экран — физическое действие
    res_fs = FastCommandRouter.match_and_execute("на весь экран")
    assert res_fs.handled is True
    assert res_fs.is_action is True

    # 4. Что сейчас играет — информационный запрос (НЕ действие, требует голосового ответа)
    res_music = FastCommandRouter.match_and_execute("что сейчас играет")
    assert res_music.handled is True
    assert res_music.is_action is False
    assert res_music.text == "Играет песня"
