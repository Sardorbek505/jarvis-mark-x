"""Локальная расшифровка команд: итог через ~0.1 с после конца речи."""

import json
import wave
from pathlib import Path

import pytest

from core.local_stt import LocalTranscriber

_VOICE = Path(__file__).resolve().parents[1] / "logs" / "latency_voice"


class _FakeRec:
    """Vosk-заглушка: закрывает сегмент на каждом третьем кадре."""

    def __init__(self):
        self.n = 0
        self.reset_calls = 0

    def SetWords(self, _):
        pass

    def AcceptWaveform(self, pcm):
        self.n += 1
        return self.n % 3 == 0

    def Result(self):
        return json.dumps({"text": f"сегмент{self.n}"})

    def FinalResult(self):
        return json.dumps({"text": "хвост"})

    def PartialResult(self):
        return json.dumps({"partial": "..."})

    def Reset(self):
        self.reset_calls += 1


def _fake_transcriber():
    t = LocalTranscriber.__new__(LocalTranscriber)
    import threading
    t._rec = _FakeRec()
    t._lock = threading.Lock()
    t._fed_bytes = 0
    t._segments = []
    t.available = True
    return t


def test_segments_closed_mid_phrase_are_kept():
    """Пауза внутри фразы закрывает сегмент Vosk — его текст не должен теряться."""
    t = _fake_transcriber()
    for _ in range(7):
        t.accept(b"\x00" * 1024)
    assert t.final() == "сегмент3 сегмент6 хвост"
    assert t.fed_seconds() == 0.0


def test_reset_drops_partial_segments():
    t = _fake_transcriber()
    for _ in range(3):
        t.accept(b"\x00" * 1024)
    t.reset()
    assert t.final() == "хвост"


@pytest.mark.skipif(not list(_VOICE.glob("*_*.wav")), reason="нет WAV стенда")
def test_real_vosk_recognizes_stand_command():
    t = LocalTranscriber()
    if not t.available:
        pytest.skip("Vosk-модель не установлена")
    wav = next(p for p in _VOICE.glob("*_86524597be.wav"))  # «Джарвис, сделай громче»
    with wave.open(str(wav)) as w:
        data = w.readframes(w.getnframes())
    for i in range(0, len(data), 1024):
        t.accept(data[i:i + 1024])
    text = t.final()
    assert "громче" in text, text


# ── Ранний арбитраж по локальной расшифровке ─────────────────────────────────

def _stub():
    import time
    from unittest.mock import MagicMock
    import main as jarvis_main
    from core.command_context import PendingGeminiTurn

    class _Stub:
        def __init__(self):
            self.logs = []
            self.spoken = []
            self.ui = MagicMock(write_log=self.logs.append, muted=False)
            self.state_machine = None
            self._wake_active_until = time.monotonic() + 5.0
            self._hotkey_active_until = 0.0
            self._pending_question = False
            self._clear_audio_in_queue = lambda: None
            self._drop_speech_prefetch = lambda: None
            self._enter_thinking = MagicMock()
            self._maybe_prefetch_speech = lambda t: None
            self._maybe_stream_speech = lambda t: None
            self._start_speech = lambda text, **kw: self.spoken.append(text)
            self._play_earcon = lambda kind: None
            self._latency = MagicMock()
            self.last_user_text = ""
            self.command_orchestrator = MagicMock(has_active_command=MagicMock(return_value=False))
            pt = PendingGeminiTurn("u", 1)
            pt.addressed = True
            pt.addressed_at = time.monotonic()
            self._pending_gemini_turn = pt

    return _Stub(), jarvis_main


def test_local_transcript_executes_command_before_gemini(monkeypatch):
    import asyncio
    from unittest.mock import patch
    from core.fast_command_router import ExecutionStatus, FastCommandResult
    stub, jm = _stub()
    with patch("core.fast_command_router.FastCommandRouter.match_and_execute",
               return_value=FastCommandResult(True, "Сделал громче, сэр.", is_action=True, status=ExecutionStatus.SUCCESS)), \
         patch("main.get_voice_provider", return_value="fish"):
        bound = jm.Jarvis._arbitrate_turn.__get__(stub, jm.Jarvis)
        handled = asyncio.run(bound("джарвис сделай громче", [], [], local_only=True))
    assert handled is True
    assert stub._pending_gemini_turn.routed_to == "LOCAL", "ход Gemini по этой фразе должен быть заглушён"


def test_local_transcript_of_a_question_leaves_turn_to_gemini(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock, patch
    from core.command_orchestrator import RoutingDecision
    from core.fast_command_router import FastCommandResult
    stub, jm = _stub()
    stub.command_orchestrator.process_user_text = MagicMock(return_value=MagicMock(decision=RoutingDecision.NEEDS_LLM))
    with patch("core.fast_command_router.FastCommandRouter.match_and_execute", return_value=FastCommandResult(False, None)):
        bound = jm.Jarvis._arbitrate_turn.__get__(stub, jm.Jarvis)
        handled = asyncio.run(bound("расскажи про марс", [], [], local_only=True))
    assert handled is False
    assert stub._pending_gemini_turn.arbitrated is False, "реплика должна остаться Gemini"
    stub._enter_thinking.assert_not_called()


def test_local_transcript_without_addressing_is_left_to_gemini():
    import asyncio
    stub, jm = _stub()
    stub._pending_gemini_turn.addressed = False
    stub._wake_active_until = 0.0
    bound = jm.Jarvis._arbitrate_turn.__get__(stub, jm.Jarvis)
    handled = asyncio.run(bound("сделай громче", [], [], local_only=True))
    assert handled is False
    assert stub._pending_gemini_turn.routed_to is None, "локальный заход не должен выносить DISCARDED"
