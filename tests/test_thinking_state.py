"""Пока Gemini думает, машина состояний стоит в THINKING, а не в LISTENING.

Стенд 16.09.2026: после арбитража реплика уходила в Gemini, а машина
оставалась в LISTENING — тикал таймер тишины, шлюз закрывался, в журнал
писалось «жду обращения по имени», и ответ приходил «из STANDBY».
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from core.audio_pipeline import AudioPipeline, THINKING_MAX_SEC
from core.conversation_state import ConversationState, ConversationStateMachine
from core.headless_ui import HeadlessUI
from main import Jarvis, PendingGeminiTurn


@pytest.fixture
def jarvis():
    ui = HeadlessUI()
    with patch("core.hotkey_manager.GlobalHotkeyManager"), \
         patch("core.wake_detector.WakeWordDetector2Stage"), \
         patch("main.Jarvis._start_telegram_bot", return_value=None):
        j = Jarvis(ui)
        j.audio_in_queue = asyncio.Queue()
        j.out_queue = asyncio.Queue()
        yield j
        j.cleanup()


def _route_to_gemini(j: Jarvis, text: str) -> bool:
    """Арбитраж без локальных роутеров: реплика заведомо уходит в Gemini."""
    j._pending_gemini_turn = PendingGeminiTurn("utt_test", 1)
    j._pending_gemini_turn.addressed = True
    j._wake_active_until = time.monotonic() + 5.0
    j.state_machine.transition_to(ConversationState.LISTENING, reason="test", force=True)
    with patch("core.fast_command_router.FastCommandRouter.match_and_execute", return_value=(False, None)), \
         patch.object(j.command_orchestrator, "process_user_text") as orch:
        orch.return_value = MagicMock(decision="NEEDS_LLM")
        from core.command_orchestrator import RoutingDecision
        orch.return_value.decision = RoutingDecision.NEEDS_LLM
        return asyncio.run(j._arbitrate_turn(text, [], []))


def test_turn_handed_to_gemini_enters_thinking(jarvis):
    is_local = _route_to_gemini(jarvis, "Джарвис, как дела?")

    assert is_local is False
    assert jarvis.state_machine.state == ConversationState.THINKING


def test_thinking_ignores_silence_timeout_and_gate():
    """В THINKING таймер тишины не тикает и закрытый шлюз не роняет в STANDBY."""
    sm = ConversationStateMachine()
    pipe = AudioPipeline(
        state_machine=sm, gateway_active_provider=lambda: False,
        enable_aec=False, enable_ducking=False, enable_endpointing=False,
        silence_timeout_sec=0.5,
    )
    pipe.wake_detector = MagicMock(process_pcm=MagicMock(return_value=False))
    sm.transition_to(ConversationState.LISTENING, reason="test", force=True)
    sm.transition_to(ConversationState.THINKING, reason="test")

    t = time.monotonic()
    for i in range(40):  # ~1.3 с тишины при кадре 32 мс
        pipe._process_frame_in_worker(b"\x00" * 1024, b"", t + i * 0.032, ConversationState.THINKING, False)

    assert sm.state == ConversationState.THINKING


def test_thinking_watchdog_returns_to_standby_when_model_never_answers():
    """Если ответа нет дольше THINKING_MAX_SEC, Джарвис не глохнет навсегда."""
    sm = ConversationStateMachine()
    pipe = AudioPipeline(
        state_machine=sm, gateway_active_provider=lambda: False,
        enable_aec=False, enable_ducking=False, enable_endpointing=False,
    )
    pipe.wake_detector = MagicMock(process_pcm=MagicMock(return_value=False))
    sm.transition_to(ConversationState.LISTENING, reason="test", force=True)
    sm.transition_to(ConversationState.THINKING, reason="test")
    sm._state_entered_at = time.monotonic() - THINKING_MAX_SEC - 1

    pipe._process_frame_in_worker(b"\x00" * 1024, b"", time.monotonic(), ConversationState.THINKING, False)

    assert sm.state == ConversationState.STANDBY
