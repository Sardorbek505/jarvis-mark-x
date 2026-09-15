import asyncio
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
            if hasattr(j._wake_detector.process_pcm, "return_value"):
                j._wake_detector.process_pcm.return_value = False
            else:
                j._wake_detector.process_pcm = MagicMock(return_value=False)
        j.audio_in_queue = asyncio.Queue()
        j.out_queue = asyncio.Queue()
        try:
            j._loop = asyncio.get_running_loop()
        except RuntimeError:
            j._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(j._loop)
        yield j
        j.cleanup()


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

    with patch.object(j, "_speak_fish", return_value=None):
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

# Порог перебивания больше не считается в main.Jarvis: за barge-in отвечает
# AudioPipeline, который решает по очищенному от эха сигналу.
# Проверки перенесены в tests/test_audio_gateway_gating.py.
