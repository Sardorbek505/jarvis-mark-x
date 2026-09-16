"""Адаптация порога речи под фактический шум комнаты.

Замер на живом микрофоне (Realtek array, тихая комната, 4 секунды):
медиана RMS 86, p90 = 201, p99 = 456 — при заданном пороге 12.0.
То есть 87% кадров ТИШИНЫ считались речью. Последствия ровно те, что
наблюдались вживую: шум комнаты непрерывно уезжал в Gemini Live (модель на
непрерывном шуме начинает выдавать реплики на случайных языках), отсчёт
молчания не начинался никогда, а фоновый пик выше 350 мог сойти за перебивание.

Фиксированное число здесь принципиально не работает: у ноутбучного массива и
у гарнитуры шумовой фон отличается на порядок. Порог считается от измеренного
фона.
"""

import time

import numpy as np

from core.audio_pipeline import (
    AudioPipeline,
    BARGE_IN_RMS_THRESHOLD,
    NOISE_MIN_SAMPLES,
)
from core.conversation_state import ConversationState, ConversationStateMachine


class _FakeWakeDetector:
    def process_pcm(self, pcm: bytes) -> bool:
        return False


def _pipeline(gate, **kwargs):
    sm = ConversationStateMachine()
    pipe = AudioPipeline(
        state_machine=sm,
        gateway_active_provider=gate,
        enable_aec=False,
        enable_ducking=False,
        **kwargs,
    )
    pipe.wake_detector = _FakeWakeDetector()
    return sm, pipe


def _frame(rms_value, samples=512):
    """Кадр с заданным RMS (постоянный уровень — RMS равен модулю значения)."""
    return np.full(samples, int(rms_value), dtype=np.int16).tobytes()


def _feed_room_noise(pipe, sm, level=90, frames=NOISE_MIN_SAMPLES + 10):
    """Прогоняет фоновый шум через STANDBY — там и измеряется фон."""
    t = time.monotonic()
    for i in range(frames):
        pipe._process_frame_in_worker(_frame(level), b"", t + i * 0.032,
                                      ConversationState.STANDBY, False)
    return sm


def test_noise_floor_is_measured_only_in_standby():
    """Фон меряется в ожидании: только там заведомо нет обращённой к нам речи."""
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0)
    assert pipe.noise_floor is None, "без наблюдений фон известен быть не может"
    assert pipe.effective_rms_threshold() == 12.0

    _feed_room_noise(pipe, sm, level=90)

    assert pipe.noise_floor is not None
    assert 80 <= pipe.noise_floor <= 100, f"фон измерен как {pipe.noise_floor}"


def test_threshold_rises_above_measured_noise():
    """Порог речи поднимается выше фона, а не остаётся угаданной константой."""
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0)
    _feed_room_noise(pipe, sm, level=90)

    threshold = pipe.effective_rms_threshold()
    assert threshold > pipe.noise_floor, "порог не выше фона — VAD пропустит тишину"
    assert threshold > 12.0, "порог остался угаданным числом"


def test_barge_in_threshold_rises_with_noise():
    """Перебивание требует запаса по шуму: пик фона не должен глушить Джарвиса.

    Замеренный p99 фона (456) выше фиксированного порога перебивания (350) —
    то есть комната сама себя объявляла бы перебиванием.
    """
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0)
    assert pipe.effective_barge_in_threshold() == BARGE_IN_RMS_THRESHOLD

    _feed_room_noise(pipe, sm, level=200)

    assert pipe.effective_barge_in_threshold() > 456, (
        "пик комнатного шума всё ещё проходит за перебивание"
    )


def test_silence_timeout_actually_fires_in_a_noisy_room():
    """Главная проверка: в шумной комнате отсчёт молчания вообще начинается.

    С угаданным порогом 12 каждый кадр фона считался речью, `_last_speech_at`
    обновлялся бесконечно, и таймаут тишины не срабатывал никогда — то есть
    починка «Джарвис сам засыпает» на реальном микрофоне не работала.
    """
    gate = {"open": True}
    closed = []
    sm, pipe = _pipeline(
        lambda: gate["open"],
        rms_threshold=12.0,
        hangover_frames=1,
        silence_timeout_sec=0.5,
        on_silence_timeout=lambda: (closed.append(True), gate.update(open=False)),
    )
    pipe.on_speech_frame = lambda f: None

    _feed_room_noise(pipe, sm, level=90)

    # Пользователь сказал слово заметно громче фона...
    t = time.monotonic()
    pipe._process_frame_in_worker(_frame(3000), b"", t, sm.state, True)
    assert sm.state == ConversationState.LISTENING

    # ...и замолчал. В комнате остался тот же фон 90.
    for i in range(1, 8):
        pipe._process_frame_in_worker(_frame(90), b"", t + i * 0.2, sm.state, gate["open"])

    assert closed == [True], "фон комнаты сошёл за речь — отсчёт молчания не начался"
    assert sm.state == ConversationState.STANDBY


def test_quiet_microphone_keeps_the_configured_threshold():
    """На тихом входе адаптация не должна поднимать порог и глушить речь."""
    sm, pipe = _pipeline(lambda: False, rms_threshold=250.0)
    _feed_room_noise(pipe, sm, level=5)

    assert pipe.effective_rms_threshold() == 250.0, (
        "на тихом микрофоне порог обязан остаться заданным"
    )


# ── Речь в STANDBY не должна считаться шумом комнаты ──────────────────────────
#
# Живой стенд 15.09.2026: «Джарвис, какая погода…» произносится ИМЕННО в STANDBY
# (ключевое слово ещё не подтверждено), и эти кадры уезжали в окно шума —
# «шум комнаты p90=2476, порог поднят с 12 до 6190». После этого микрофон
# оглох: третья реплика отвергнута как «речи в комнате не было».


class _FakeEndpointer:
    """Silero-заглушка: громкий кадр = речь, тихий = фон."""
    available = True

    def __init__(self, speech_rms=500):
        self.speech_rms = speech_rms

    def speech_probability(self, pcm: bytes) -> float:
        rms = float(np.abs(np.frombuffer(pcm, dtype=np.int16)).mean())
        return 0.95 if rms >= self.speech_rms else 0.02

    def process(self, pcm: bytes, frame_ms: float):
        return self.speech_probability(pcm) > 0.5, False

    def reset(self):
        pass


def _feed(pipe, state, level, frames, t0=None):
    t = t0 if t0 is not None else time.monotonic()
    for i in range(frames):
        pipe._process_frame_in_worker(_frame(level), b"", t + i * 0.032, state, False)
    return t + frames * 0.032


def test_speech_in_standby_does_not_poison_noise_floor():
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0)
    pipe.endpointer = _FakeEndpointer()

    _feed(pipe, ConversationState.STANDBY, level=90, frames=NOISE_MIN_SAMPLES + 10)
    # Человек говорит «Джарвис…» — 30 громких кадров ~1 с, всё ещё в STANDBY
    _feed(pipe, ConversationState.STANDBY, level=2500, frames=30)

    assert pipe.noise_floor is not None
    assert pipe.noise_floor < 200, (
        f"речь пользователя измерена как шум комнаты: p90={pipe.noise_floor}"
    )
    assert pipe.effective_rms_threshold() < 1000


def test_noise_floor_without_endpointer_is_robust_to_speech():
    """Без Silero нечем отличить речь от фона — оценка должна быть устойчивой к ней."""
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0, enable_endpointing=False)
    pipe.endpointer = None

    _feed(pipe, ConversationState.STANDBY, level=90, frames=NOISE_MIN_SAMPLES + 40)
    _feed(pipe, ConversationState.STANDBY, level=2500, frames=30)

    assert pipe.noise_floor < 200, f"фон оценён по речи: {pipe.noise_floor}"


def test_wake_word_is_not_vetoed_by_stale_speech_window():
    """Окно вероятности речи, заполненное в STANDBY, устаревает, пока Джарвис говорит.

    Стенд: wake-word пришёл на границе SPEAKING → FOLLOW_UP и был отвергнут по
    значениям полусекундной давности из STANDBY (max p=0.03).
    """
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0)
    pipe.endpointer = _FakeEndpointer()
    woke = []
    pipe.on_wake = lambda *a, **k: woke.append(True)

    t = _feed(pipe, ConversationState.STANDBY, level=30, frames=20)  # тихо, p=0.02
    sm.transition_to(ConversationState.SPEAKING, reason="test")
    _feed(pipe, ConversationState.SPEAKING, level=30, frames=40, t0=t + 5.0)

    pipe._handle_wake_spotted(1.0)

    assert woke, "ключевое слово отвергнуто по протухшему окну речи"
    assert pipe.wake_vetoed_count == 0


def test_wake_word_in_follow_up_uses_fresh_speech_probability():
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0)
    pipe.endpointer = _FakeEndpointer()
    woke = []
    pipe.on_wake = lambda *a, **k: woke.append(True)

    t = _feed(pipe, ConversationState.STANDBY, level=30, frames=20)
    sm.transition_to(ConversationState.SPEAKING, reason="test")
    sm.transition_to(ConversationState.FOLLOW_UP, reason="test")
    # В follow-up человек говорит — вероятность речи должна обновляться и здесь
    _feed(pipe, ConversationState.FOLLOW_UP, level=5, frames=3, t0=t)
    pipe._speech_prob_window.clear()
    pipe._speech_prob_window.append(0.9)

    pipe._handle_wake_spotted(1.0)
    assert woke and pipe.wake_vetoed_count == 0


def test_silent_room_still_vetoes_phantom_wake_word():
    """Вето остаётся рабочим: свежая тишина в STANDBY по-прежнему гасит ложное срабатывание."""
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0)
    pipe.endpointer = _FakeEndpointer()
    woke = []
    pipe.on_wake = lambda *a, **k: woke.append(True)

    _feed(pipe, ConversationState.STANDBY, level=30, frames=20)
    pipe._handle_wake_spotted(1.0)

    assert not woke and pipe.wake_vetoed_count == 1


def test_silence_veto_does_not_apply_to_neural_wake():
    """Своя модель узнала слово — Silero-вето не голосует (при играющих колонках
    эхоподавитель давит речь, и p≈0.05 при score 1.00 — живой прогон 17.09.2026)."""
    sm, pipe = _pipeline(lambda: False, rms_threshold=12.0)
    pipe.endpointer = _FakeEndpointer()
    woke = []
    pipe.on_wake = lambda *a, **k: woke.append(True)
    _feed(pipe, ConversationState.STANDBY, level=30, frames=20)   # тишина по Silero
    pipe.wake_detector.last_wake_source = "nn"
    pipe._handle_wake_spotted(1.0)
    assert woke and pipe.wake_vetoed_count == 0

    woke.clear()
    pipe.state_machine.transition_to(ConversationState.STANDBY, reason="test", force=True)
    _feed(pipe, ConversationState.STANDBY, level=30, frames=20)   # окно вероятностей снова свежее и тихое
    pipe.wake_detector.last_wake_source = "vosk"
    pipe._handle_wake_spotted(1.0)
    assert not woke and pipe.wake_vetoed_count == 1, "для Vosk вето остаётся"
