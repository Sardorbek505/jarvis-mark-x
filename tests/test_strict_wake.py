"""Строгий режим обращения: Джарвис слушает только после «Джарвис» (как Алиса).

Стенд 16.09.2026 (жалоба владельца): реагировал на разговор в комнате. Пять
дыр — см. core/wake_policy.py. Здесь каждая закрывается отдельным тестом.
"""

import time

import pytest

from core import wake_policy as wp


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setenv("JARVIS_STRICT_WAKE", "1")


@pytest.fixture
def soft(monkeypatch):
    monkeypatch.setenv("JARVIS_STRICT_WAKE", "0")


# ── Политика ─────────────────────────────────────────────────────────────────

def test_no_follow_up_window_after_plain_answer_in_strict(strict):
    assert wp.follow_up_window(asked_question=False) == 0.0


def test_follow_up_window_stays_when_jarvis_asked(strict):
    assert wp.follow_up_window(asked_question=True) == wp.FOLLOW_UP_AFTER_QUESTION_SEC


def test_soft_mode_keeps_short_window(soft):
    assert wp.follow_up_window(asked_question=False) == wp.FOLLOW_UP_AFTER_ANSWER_SOFT_SEC


def test_loud_room_does_not_open_mic_in_strict(strict):
    assert wp.rms_barge_in_enabled() is False
    assert wp.gate_after_interrupt("voice-rms-barge-in") == 0.0
    assert wp.gate_after_interrupt("wake-word-barge-in") == 8.0


def test_phrase_without_name_is_ignored_in_strict(strict):
    assert wp.is_addressed(name_in_text=False, wake_spotted=False, wake_to_speech_gap=None, gate_open=True) is False


def test_wake_then_immediate_speech_is_addressed_even_without_name_in_text(strict):
    """Имя съел детектор, в расшифровке «, какая погода» — но речь началась сразу за словом."""
    assert wp.is_addressed(name_in_text=False, wake_spotted=True, wake_to_speech_gap=0.8) is True


def test_wake_then_long_silence_then_speech_is_not_addressed(strict):
    """Ложное срабатывание от телевизора, а через 5 с кто-то заговорил — не к нам."""
    assert wp.is_addressed(name_in_text=False, wake_spotted=True, wake_to_speech_gap=12.0) is False


def test_name_in_text_is_always_addressed(strict):
    assert wp.is_addressed(name_in_text=True, wake_spotted=False, wake_to_speech_gap=None) is True


def test_answer_to_jarvis_question_is_addressed(strict):
    assert wp.is_addressed(name_in_text=False, wake_spotted=False, wake_to_speech_gap=None, active_dialog=True) is True


# ── Vosk: только финальные результаты и точное слово ─────────────────────────

def test_vosk_partial_results_do_not_trigger_wake(monkeypatch):
    from core.wake_detector import WakeWordDetector2Stage
    det = WakeWordDetector2Stage.__new__(WakeWordDetector2Stage)
    assert det._vosk_word_is_wake("джарвис") is True
    assert det._vosk_word_is_wake("жарвис") is True
    assert det._vosk_word_is_wake("джарвисом") is False, "падежная форма — не обращение, а рассказ о нём"
    assert det._vosk_word_is_wake("джарви") is False, "обрывок промежуточного результата"


# ── Интеграция с арбитражем ──────────────────────────────────────────────────

def test_arbitration_discards_room_speech_in_strict(strict):
    import asyncio
    from unittest.mock import MagicMock
    import main as jarvis_main
    from core.command_context import PendingGeminiTurn

    class _Stub:
        def __init__(self):
            self.logs = []
            self.ui = MagicMock(write_log=self.logs.append, muted=False)
            self.state_machine = None
            self._wake_active_until = time.monotonic() + 3.0   # окно после ответа ещё открыто
            self._hotkey_active_until = 0.0
            self._pending_question = False
            self._clear_audio_in_queue = lambda: None
            self.command_orchestrator = MagicMock(has_active_command=MagicMock(return_value=False))
            self._pending_gemini_turn = PendingGeminiTurn("u", 1)

    stub = _Stub()
    bound = jarvis_main.Jarvis._arbitrate_turn.__get__(stub, jarvis_main.Jarvis)
    is_local = asyncio.run(bound("а ты видел вчера матч", [], []))
    assert is_local is False
    assert stub._pending_gemini_turn.routed_to == "DISCARDED"
    assert any("Игнор" in str(log) for log in stub.logs)


def test_arbitration_accepts_wake_then_speech(strict):
    import asyncio
    from unittest.mock import MagicMock, patch
    import main as jarvis_main
    from core.command_context import PendingGeminiTurn

    class _Stub:
        def __init__(self):
            self.logs = []
            self.ui = MagicMock(write_log=self.logs.append, muted=False)
            self.state_machine = None
            self._wake_active_until = 0.0
            self._hotkey_active_until = 0.0
            self._pending_question = False
            self._clear_audio_in_queue = lambda: None
            self._drop_speech_prefetch = lambda: None
            self._enter_thinking = lambda: None
            self._maybe_prefetch_speech = lambda t: None
            self._maybe_stream_speech = lambda t: None
            self.command_orchestrator = MagicMock(has_active_command=MagicMock(return_value=False))
            pt = PendingGeminiTurn("u", 1)
            pt.addressed = True
            pt.addressed_at = time.monotonic() - 1.0
            pt.first_transcript_at = time.monotonic() - 0.4
            self._pending_gemini_turn = pt
            self.last_user_text = ""

    stub = _Stub()
    with patch("core.fast_command_router.FastCommandRouter.match_and_execute",
               return_value=jarvis_main.FastCommandResult(False, None)) if hasattr(jarvis_main, "FastCommandResult") else patch("core.fast_command_router.FastCommandRouter.match_and_execute", return_value=(False, None)):
        from core.command_orchestrator import RoutingDecision
        stub.command_orchestrator.process_user_text = MagicMock(return_value=MagicMock(decision=RoutingDecision.NEEDS_LLM))
        bound = jarvis_main.Jarvis._arbitrate_turn.__get__(stub, jarvis_main.Jarvis)
        asyncio.run(bound(", какая сегодня погода в Шымкенте", [], []))
    assert stub._pending_gemini_turn.routed_to == "GEMINI"


def test_silence_after_false_wake_cancels_addressing(strict):
    """Слово «сработало», речи не было, таймер тишины закрыл шлюз — обращение снято."""
    import main as jarvis_main
    from core.command_context import PendingGeminiTurn

    class _Stub:
        _wake_active_until = 10.0
        _hotkey_active_until = 0.0

    stub = _Stub()
    stub._pending_gemini_turn = PendingGeminiTurn("u", 1)
    stub._pending_gemini_turn.addressed = True
    stub._pending_gemini_turn.addressed_at = time.monotonic()
    jarvis_main.Jarvis._on_listen_silence_timeout.__get__(stub, jarvis_main.Jarvis)()
    assert stub._pending_gemini_turn.addressed is False


def test_silence_timeout_keeps_addressing_once_speech_started(strict):
    import main as jarvis_main
    from core.command_context import PendingGeminiTurn

    class _Stub:
        _wake_active_until = 10.0
        _hotkey_active_until = 0.0

    stub = _Stub()
    stub._pending_gemini_turn = PendingGeminiTurn("u", 1)
    stub._pending_gemini_turn.addressed = True
    stub._pending_gemini_turn.first_transcript_at = time.monotonic()
    jarvis_main.Jarvis._on_listen_silence_timeout.__get__(stub, jarvis_main.Jarvis)()
    assert stub._pending_gemini_turn.addressed is True


@pytest.mark.parametrize("text,expected", [
    ("Чарли, который час.", "который час."),
    ("Чарлись, который сейчас.", "который сейчас."),
    ("Джарвис, сделай громче", "сделай громче"),
    ("Включи музыку, пожалуйста", "Включи музыку, пожалуйста"),   # два слова перед запятой — не имя
    ("который час", "который час"),
])
def test_misheard_vocative_is_stripped_after_wake(text, expected):
    from core.wake_names import strip_vocative
    assert strip_vocative(text, wake_spotted=True) == expected


def test_vocative_kept_without_wake():
    from core.wake_names import strip_vocative
    assert strip_vocative("Чарли, который час.", wake_spotted=False) == "Чарли, который час."


def test_local_time_handles_misheard_name():
    from core.fast_command_router import FastCommandRouter
    from core.wake_names import strip_vocative
    res = FastCommandRouter.match_and_execute(strip_vocative("Чарлись, который сейчас.", True))
    assert res[0] is True and res[1].startswith("Сейчас ")
