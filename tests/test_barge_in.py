import asyncio
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
import time

from core.headless_ui import HeadlessUI
from main import Jarvis


@pytest.fixture
def jarvis_instance():
    ui = HeadlessUI()
    with patch("core.hotkey_manager.GlobalHotkeyManager"), \
         patch("core.wake_detector.WakeWordDetector2Stage"), \
         patch("main.Jarvis._start_telegram_bot", return_value=None):
        j = Jarvis(ui)
        if j._wake_detector:
            j._wake_detector.process_pcm.return_value = False
        j.audio_in_queue = asyncio.Queue()
        j.out_queue = asyncio.Queue()
        try:
            j._loop = asyncio.get_running_loop()
        except RuntimeError:
            j._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(j._loop)
        return j


def test_interrupt_speech_resets_state_and_drains_queue(jarvis_instance):
    j = jarvis_instance
    j._is_speaking = True
    j._active_synth_tasks = 2
    j.audio_in_queue.put_nowait(b"chunk1")
    j.audio_in_queue.put_nowait(b"chunk2")

    mock_task = MagicMock()
    mock_task.done.return_value = False
    j._active_speech_tasks = {mock_task}

    with patch("core.earcons.play_success_earcon") as mock_earcon:
        j.interrupt_speech("test-reason")

        assert j._is_speaking is False
        assert j._active_synth_tasks == 0
        assert j.audio_in_queue.empty()
        assert j._speech_epoch >= 1
        assert j._interrupted_turn is True
        assert j._wake_active_until > time.monotonic()
        assert j.ui.state == "LISTENING"
        mock_task.cancel.assert_called_once()
        mock_earcon.assert_called_once()


def test_interrupt_speech_noop_when_idle(jarvis_instance):
    j = jarvis_instance
    j._is_speaking = False
    j._active_synth_tasks = 0
    assert j.audio_in_queue.empty()
    initial_epoch = getattr(j, "_speech_epoch", 0)

    with patch("core.earcons.play_success_earcon") as mock_earcon:
        j.interrupt_speech("noop")
        assert getattr(j, "_speech_epoch", 0) == initial_epoch
        mock_earcon.assert_not_called()


@pytest.mark.asyncio
async def test_start_speech_registers_task_and_updates_epoch(jarvis_instance):
    j = jarvis_instance
    initial_epoch = getattr(j, "_speech_epoch", 0)

    with patch.object(j, "_speak_fish", return_value=None) as mock_speak:
        task = j._start_speech("Привет мир")
        assert task is not None
        assert j._speech_epoch == initial_epoch + 1
        assert j._interrupted_turn is False
        assert task in j._active_speech_tasks
        await task
        # After completion, callback removes it
        assert task not in j._active_speech_tasks


@pytest.mark.asyncio
async def test_speak_fish_aborts_immediately_on_barge_in(jarvis_instance):
    j = jarvis_instance
    j._speech_epoch = 1
    epoch = j._speech_epoch

    async def fake_speak_pcm(fragment, sample_rate=24000):
        await asyncio.sleep(0.05)
        return b"\x00" * 4800

    with patch("telegram_bot.tts_fish.is_configured", return_value=True), \
         patch("telegram_bot.tts_fish.speak_pcm", side_effect=fake_speak_pcm), \
         patch("main._split_for_speech", return_value=["Фрагмент 1", "Фрагмент 2", "Фрагмент 3"]):

        speak_task = asyncio.create_task(j._speak_fish("Текст", epoch=epoch))
        await asyncio.sleep(0.01)
        assert j._is_speaking is True

        # Simulate user barge-in
        j.interrupt_speech("user-voice-interruption")

        await speak_task

        # Since interruption happened, subsequent fragments are discarded and queue emptied
        assert j.audio_in_queue.empty()
        assert j._active_synth_tasks == 0


def test_quick_command_triggers_barge_in(jarvis_instance):
    j = jarvis_instance
    j._is_speaking = True
    j.audio_in_queue.put_nowait(b"audio")

    with patch("core.fast_command_router.FastCommandRouter.match_and_execute") as mock_router, \
         patch("core.earcons.play_success_earcon"):
        mock_router.return_value = (True, "")
        j._handle_quick_command("стоп")

        assert j._is_speaking is False
        assert j.audio_in_queue.empty()


def test_check_barge_in_adaptive_threshold(jarvis_instance):
    j = jarvis_instance
    # 1. Quiet noise frame (RMS = 100.0) -> No interrupt
    noise_frame = (np.ones(512, dtype=np.int16) * 100)
    should, reason = j._check_barge_in(noise_frame)
    assert should is False
    assert reason == ""

    # 2. Strong voice frame (RMS = 500.0) with quiet speaker -> Barge-in triggered
    voice_frame = (np.ones(512, dtype=np.int16) * 500)
    should, reason = j._check_barge_in(voice_frame)
    assert should is True
    assert reason == "voice-rms-barge-in"

    # 3. Loud speaker active (peak = 0.5) -> dynamic threshold = 0.5 * 2400 = 1200
    # Voice frame with RMS = 500 is now suppressed (preventing false echo triggers)
    mock_meter = MagicMock()
    mock_meter.peak = 0.5
    j._speaker_meter = mock_meter
    should, reason = j._check_barge_in(voice_frame)
    assert should is False

    # 4. Louder voice (RMS = 1400 > 1200) cuts through even when assistant speaks loudly
    loud_voice = (np.ones(512, dtype=np.int16) * 1400)
    should, reason = j._check_barge_in(loud_voice)
    assert should is True
    assert reason == "voice-rms-barge-in"

    # 5. Quiet voice with wake-word / stop detected by wake_detector
    j._wake_detector = MagicMock()
    j._wake_detector.process_pcm.return_value = True
    should, reason = j._check_barge_in(noise_frame)
    assert should is True
    assert reason == "wake-word-barge-in"
