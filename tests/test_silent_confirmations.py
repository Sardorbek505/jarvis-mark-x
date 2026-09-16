"""Очевидные действия подтверждаются сигналом, а не фразой (как у Алисы)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from core import confirmations as c
from core.command_orchestrator import ProcessResult, RoutingDecision
from core.fast_command_router import ExecutionStatus, FastCommandResult
import main as jarvis_main


@pytest.mark.parametrize("text,bare", [
    ("Готово, сэр.", True), ("Готово.", True), ("Сделано, сэр", True), ("Включаю, сэр.", True),
    ("Готово, таймер на пять минут поставлен, сэр.", False),
    ("Не удалось включить, сэр.", False), ("Какой сезон, сэр?", False), ("", False),
])
def test_bare_confirmation_detection(text, bare):
    assert c.is_bare_confirmation(text) is bare


def test_perceivable_success_is_silent_but_failures_and_timers_speak():
    assert c.should_stay_silent("play_media", True, "Включаю «Гладиатор», сэр.") is True
    assert c.should_stay_silent("play_music", True, "Включил numb, сэр.") is True
    assert c.should_stay_silent("play_media", False, "Не нашёл фильм, сэр.") is False
    assert c.should_stay_silent("play_music", True, "Не удалось найти трек, сэр.") is False
    assert c.should_stay_silent("sleep_timer", True, "Таймер сна на 30 минут, сэр.") is False


# ── Арбитраж: fast-path и оркестратор ─────────────────────────────────────────

class _Stub:
    def __init__(self):
        self.spoken = []
        self.logs = []
        self.ui = MagicMock(write_log=self.logs.append, muted=False)
        self.state_machine = None
        self._pending_gemini_turn = None
        self._wake_active_until = 0.0
        self._hotkey_active_until = 0.0
        self._start_speech = lambda text, **kw: self.spoken.append(text)
        self._drop_speech_prefetch = lambda: None
        self._clear_audio_in_queue = lambda: None
        self._enter_thinking = lambda: None
        self._pending_question = False
        self._followup_timeout = None
        self.command_orchestrator = MagicMock(has_active_command=MagicMock(return_value=False))
        self._play_earcon = jarvis_main.Jarvis._play_earcon.__get__(self, jarvis_main.Jarvis)


def _arbitrate(stub, fast=None, orch=None):
    stub._wake_active_until = 10**12
    with patch("core.fast_command_router.FastCommandRouter.match_and_execute",
               return_value=fast or FastCommandResult(False, None)), \
         patch("main.get_voice_provider", return_value="fish"), \
         patch("core.earcons.play_success_earcon") as ok, \
         patch("core.earcons.play_error_earcon") as err:
        stub.command_orchestrator.process_user_text = MagicMock(return_value=orch)
        bound = jarvis_main.Jarvis._arbitrate_turn.__get__(stub, jarvis_main.Jarvis)
        asyncio.run(bound("джарвис, тест", [], []))
    return ok, err


def test_successful_media_action_is_only_an_earcon():
    stub = _Stub()
    ok, err = _arbitrate(stub, orch=ProcessResult(decision=RoutingDecision.CONSUMED_ACTION,
                                                 executed_result="Включаю «Гладиатор», сэр.",
                                                 action_name="play_media", success=True))
    assert stub.spoken == [], "очевидное действие не озвучивается"
    ok.assert_called_once()
    err.assert_not_called()


def test_failed_media_action_speaks_and_plays_error_earcon():
    stub = _Stub()
    ok, err = _arbitrate(stub, orch=ProcessResult(decision=RoutingDecision.CONSUMED_ACTION,
                                                 executed_result="Не нашёл фильм, сэр.",
                                                 action_name="play_media", success=False))
    assert stub.spoken == ["Не нашёл фильм, сэр."]
    err.assert_called_once()
    ok.assert_not_called()


def test_timer_action_still_speaks():
    stub = _Stub()
    _arbitrate(stub, orch=ProcessResult(decision=RoutingDecision.CONSUMED_ACTION,
                                        executed_result="Таймер сна на 30 минут, сэр.",
                                        action_name="sleep_timer", success=True))
    assert stub.spoken == ["Таймер сна на 30 минут, сэр."]


def test_failed_fast_action_speaks_error():
    """Быстрая команда упала — молчать нельзя: пользователь ждёт результата."""
    stub = _Stub()
    ok, err = _arbitrate(stub, fast=FastCommandResult(True, "Не удалось переключить трек, сэр.",
                                                      is_action=True, status=ExecutionStatus.FAILED))
    assert stub.spoken == ["Не удалось переключить трек, сэр."]
    err.assert_called_once()


def test_successful_fast_action_stays_silent():
    stub = _Stub()
    _arbitrate(stub, fast=FastCommandResult(True, "Сделал громче, сэр.", is_action=True,
                                            status=ExecutionStatus.SUCCESS))
    assert stub.spoken == []


def test_multi_word_bare_confirmation():
    assert c.is_bare_confirmation("Готово. Сделано.") is True
    assert c.is_bare_confirmation("Готово. Сделано, сэр.") is True
    assert c.is_bare_confirmation("Сэр.") is False


@pytest.mark.parametrize("text", ["Жарвиз, сделай громче", "Джарвиз, закрой фильм", "Джарвист, пауза"])
def test_misheard_name_is_stripped_by_fast_path(text):
    from core.fast_command_router import normalize_command_text
    assert not normalize_command_text(text).startswith(("жарв", "джарв"))
    assert jarvis_main.is_addressed_to_jarvis(text)
