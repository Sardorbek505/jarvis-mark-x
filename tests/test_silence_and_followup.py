"""Защита от «болтовни»: закрытие шлюза по тишине и корректное окно follow-up.

Оба сценария взяты из живого сбоя, а не придуманы: Джарвис на скриншоте
бесконечно переспрашивал и выдавал реплики на случайных языках. Причины две —
шлюз не закрывался, пока не истечёт окно wake-слова (комнатный шум всё это
время шёл в Gemini Live), и укороченное окно для шумового переспроса терялось
по дороге, потому что при голосе Fish окно открывается в другом месте кода.
"""

import time

import numpy as np

from core.audio_pipeline import AudioPipeline
from core.conversation_state import ConversationState, ConversationStateMachine


class _FakeWakeDetector:
    def __init__(self):
        self.calls = 0

    def process_pcm(self, pcm: bytes) -> bool:
        self.calls += 1
        return False


def _pipeline(gate, **kwargs):
    sm = ConversationStateMachine()
    pipe = AudioPipeline(
        state_machine=sm,
        gateway_active_provider=gate,
        enable_aec=False,
        enable_ducking=False,
        # Здесь проверяется ШЛЮЗ и энергетический откат, а не распознавание речи.
        # Silero справедливо не считает речью синтетический кадр постоянного
        # уровня, и с ним эти тесты проверяли бы совсем другое.
        # Нейросетевой эндпоинтинг покрыт в tests/test_endpointing_and_latency.py.
        enable_endpointing=False,
        **kwargs,
    )
    pipe.wake_detector = _FakeWakeDetector()
    return sm, pipe


def _frame(value, samples=512):
    return np.full(samples, value, dtype=np.int16).tobytes()


def test_silence_closes_the_gate_before_wake_window_expires():
    """Затянувшаяся тишина закрывает шлюз, не дожидаясь конца окна wake-слова."""
    gate = {"open": True}
    closed = []
    sm, pipe = _pipeline(
        lambda: gate["open"],
        rms_threshold=100.0,
        hangover_frames=1,
        silence_timeout_sec=0.5,
        # Именно это делает рантайм: обнуляет своё окно активности.
        # Без закрытия шлюза следующий же кадр открыл бы LISTENING обратно.
        on_silence_timeout=lambda: (closed.append(True), gate.update(open=False)),
    )
    sent = []
    pipe.on_speech_frame = sent.append

    t = time.monotonic()
    # Пользователь сказал одно слово...
    pipe._process_frame_in_worker(_frame(4000), b"", t, sm.state, True)
    assert sm.state == ConversationState.LISTENING
    assert sent, "речь обязана уходить в облако"

    # ...и замолчал. Шлюз при этом формально ещё открыт.
    sent.clear()
    for i in range(1, 6):
        pipe._process_frame_in_worker(_frame(0), b"", t + i * 0.2, sm.state, gate["open"])

    assert closed == [True], "рантайму не сообщили, что пора закрыть окно активности"
    assert sm.state == ConversationState.STANDBY, "машина осталась слушать тишину"
    # Поток не обрывается — но всё, что после хвоста VAD, это цифровая тишина,
    # а не звук комнаты.
    assert sent, "поток в облако оборвался"
    assert all(set(кадр) == {0} for кадр in sent[1:]), (
        "после хвоста VAD в облако ушёл звук комнаты"
    )


def test_speech_resets_the_silence_countdown():
    """Пока человек говорит, отсчёт тишины не копится."""
    closed = []
    sm, pipe = _pipeline(
        lambda: True,
        rms_threshold=100.0,
        hangover_frames=1,
        silence_timeout_sec=0.5,
        on_silence_timeout=lambda: closed.append(True),
    )
    pipe.on_speech_frame = lambda f: None

    t = time.monotonic()
    # Речь с короткими паузами: суммарно дольше таймаута, подряд — нет.
    for i in range(12):
        value = 4000 if i % 3 == 0 else 0
        pipe._process_frame_in_worker(_frame(value), b"", t + i * 0.15, sm.state, True)

    assert closed == [], "отсчёт тишины не сбрасывается на речи — фразу обрежет посередине"
    assert sm.state == ConversationState.LISTENING

# Окно follow-up для шумового переспроса проверяется на реальном прогоне
# _receive_audio — в tests/test_voice_loop_e2e.py.
