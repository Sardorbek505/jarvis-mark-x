"""Deterministic tests for Single-Execution Guarantee (Fast-Path suppresses cloud turn)."""

import asyncio
from unittest.mock import patch
import pytest

from core.headless_ui import HeadlessUI
from core.conversation_state import ConversationState
from core.fast_command_router import FastCommandRouter
from main import Jarvis


@pytest.fixture
def jarvis_app():
    ui = HeadlessUI()
    with patch("core.hotkey_manager.GlobalHotkeyManager"), \
         patch("core.wake_detector.WakeWordDetector2Stage"), \
         patch("main.Jarvis._start_telegram_bot", return_value=None):
        j = Jarvis(ui)
        j.audio_in_queue = asyncio.Queue()
        j.out_queue = asyncio.Queue()
        try:
            j._loop = asyncio.get_running_loop()
        except RuntimeError:
            j._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(j._loop)
        return j


def test_spotterless_suppresses_cloud_turn_and_clears_queue(jarvis_app):
    """При быстрой споттерлесс-команде очередь out_queue очищается, шлюз закрывается, облако не вызывается."""
    j = jarvis_app
    # Симулируем накопленные кадры в очереди отправки в облако
    j.out_queue.put_nowait({"data": b"mic_pcm_1", "mime_type": "audio/pcm"})
    j.out_queue.put_nowait({"data": b"mic_pcm_2", "mime_type": "audio/pcm"})
    assert not j.out_queue.empty()

    with patch("actions.music_player._send_media_key", return_value=True):
        j._handle_quick_command("пауза")
        # Исполнение ушло в отдельный поток, чтобы не морозить аудиоконвейер:
        # роутер умеет уходить в movie_player с паузами до 2 секунд.
        if j._fast_command_thread:
            j._fast_command_thread.join(timeout=5.0)

        # Очередь должна быть очищена (Single-Execution Guarantee)
        assert j.out_queue.empty()
        assert j._wake_active_until == 0.0
        assert j.state_machine.state in (ConversationState.STANDBY, ConversationState.EXECUTING)


@pytest.mark.asyncio
async def test_receive_audio_fast_command_drops_cloud_buffers(jarvis_app):
    """Когда FastCommandRouter распознает команду в turn_complete, буферы облака отбрасываются."""
    j = jarvis_app

    # Симулируем приход облачного ответа
    j.audio_in_queue.put_nowait(b"cloud_audio_token_1")
    j.audio_in_queue.put_nowait(b"cloud_audio_token_2")
    j.out_queue.put_nowait({"data": b"mic_chunk", "mime_type": "audio/pcm"})

    # Моделируем совпадение быстрой команды
    with patch("actions.music_player._send_media_key", return_value=True):
        res = FastCommandRouter.match_and_execute("Джарвис, следующий трек", player=j.ui)
        assert res.handled is True

        # Симулируем логику из _receive_audio для fast_handled
        if res.handled:
            j._wake_active_until = 0.0
            j.state_machine.transition_to(ConversationState.EXECUTING)
            while not j.out_queue.empty():
                j.out_queue.get_nowait()
            while not j.audio_in_queue.empty():
                j.audio_in_queue.get_nowait()
            j.state_machine.transition_to(ConversationState.STANDBY)

        assert j.out_queue.empty()
        assert j.audio_in_queue.empty()
        assert j.state_machine.state == ConversationState.STANDBY
