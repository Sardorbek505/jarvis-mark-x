"""Отклик только на голос владельца (JARVIS_OWNER_ONLY=1)."""

import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from core import speaker_verifier as sv
from core.audio_pipeline import AudioPipeline
from core.conversation_state import ConversationState, ConversationStateMachine


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_OWNER_ONLY", raising=False)
    assert sv.get_speaker_verifier() is None


def test_short_audio_is_trusted():
    """Мало речи — эмбеддинг ненадёжен: не глушим ассистента из-за своей проверки."""
    v = sv.SpeakerVerifier(enrollment=np.ones(256))
    ok, score = v.is_owner(np.zeros(1000, np.int16))
    assert ok is True and score is None


def test_threshold_decides(monkeypatch):
    v = sv.SpeakerVerifier(enrollment=np.ones(256), threshold=0.6)
    assert v.enrollment.shape == (1, 256)
    monkeypatch.setattr(v, "embed", lambda pcm: np.ones(256) * 0.9)   # cos = 1.0
    assert v.is_owner(np.zeros(32000, np.int16)) == (True, pytest.approx(1.0))
    e = np.ones(256)
    e[:128] = -1  # cos = 0
    monkeypatch.setattr(v, "embed", lambda pcm: e)
    assert v.is_owner(np.zeros(32000, np.int16))[0] is False


class _Endpointer:
    available = True

    def __init__(self):
        self.calls = 0

    def process(self, pcm, frame_ms):
        self.calls += 1
        if self.calls < 20:
            return (True, False)
        return (False, self.calls == 20)   # ровно 20-й кадр закрывает фразу

    def speech_probability(self, pcm):
        return 0.9

    def reset(self):
        self.calls = 0


def _pipeline(verifier, transcripts, foreign):
    sm = ConversationStateMachine()
    pipe = AudioPipeline(
        state_machine=sm, gateway_active_provider=lambda: True,
        on_speech_frame=lambda f: None, on_local_transcript=transcripts.append,
        on_foreign_voice=foreign.append, enable_aec=False, enable_ducking=False,
        enable_endpointing=False, enable_local_stt=False,
    )
    pipe.endpointer = _Endpointer()
    pipe.local_stt = MagicMock(fed_seconds=MagicMock(return_value=2.0), final=MagicMock(return_value="сделай громче"))
    pipe.speaker_verifier = verifier
    pipe.wake_detector = MagicMock(process_pcm=MagicMock(return_value=False))
    sm.transition_to(ConversationState.LISTENING, reason="test", force=True)
    return pipe


def _feed(pipe, frames=25):
    t = time.monotonic()
    for i in range(frames):
        pipe._process_frame_in_worker(b"\x10\x00" * 1024, b"", t + i * 0.064, ConversationState.LISTENING, True)


def test_foreign_voice_is_dropped_before_local_command():
    transcripts, foreign = [], []
    verifier = MagicMock(is_owner=MagicMock(return_value=(False, 0.31)))
    pipe = _pipeline(verifier, transcripts, foreign)
    _feed(pipe)
    assert transcripts == [], "команда чужим голосом не должна дойти до роутера"
    assert foreign == [0.31]


def test_owner_voice_passes():
    transcripts, foreign = [], []
    verifier = MagicMock(is_owner=MagicMock(return_value=(True, 0.81)))
    pipe = _pipeline(verifier, transcripts, foreign)
    _feed(pipe)
    assert transcripts == ["сделай громче"]
    assert foreign == []
    audio = verifier.is_owner.call_args[0][0]
    assert len(audio) >= 1024 * 10, "на проверку должна уйти вся реплика, а не последний кадр"


def test_foreign_voice_closes_gate_in_runtime():
    import main as jarvis_main
    from core.command_context import PendingGeminiTurn

    class _Stub:
        _wake_active_until = time.monotonic() + 5
        _hotkey_active_until = 0.0

    stub = _Stub()
    stub.ui = MagicMock()
    stub._pending_gemini_turn = PendingGeminiTurn("u", 1)
    stub._pending_gemini_turn.addressed = True
    jarvis_main.Jarvis._on_foreign_voice.__get__(stub, jarvis_main.Jarvis)(0.3)
    assert stub._pending_gemini_turn.addressed is False
    assert stub._pending_gemini_turn.routed_to == "DISCARDED", "имя в расшифровке Gemini не должно спасти чужую реплику"
    assert stub._wake_active_until <= time.monotonic()


def test_bank_scoring_uses_top3_nearest():
    bank = np.zeros((5, 256))
    bank[0, 0] = 1.0                 # одна запись — как проверяемый голос
    bank[1:, 1] = 1.0                # четыре — совсем другие
    v = sv.SpeakerVerifier(enrollment=bank, threshold=0.62)
    probe = np.zeros(256)
    probe[0] = 1.0
    v.embed = lambda pcm: probe
    # top-3 из [1, 0, 0, 0, 0] → (1+0+0)/3 ≈ 0.33: одна похожая запись не решает
    assert v.score(np.zeros(32000, np.int16)) == pytest.approx(1 / 3)
