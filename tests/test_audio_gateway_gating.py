"""Шлюз микрофона: связь ConversationStateMachine и окна активности.

Здесь ловится тот класс дефектов, из-за которого Джарвис «работал, но с
проблемами»: машина состояний уходила в LISTENING и не возвращалась обратно.
Внешне всё живо, а по факту ключевое слово больше не требуется и весь звук
из комнаты непрерывно уезжает в облако.
"""

import time

import numpy as np

from core.audio_pipeline import AudioPipeline
from core.conversation_state import ConversationState, ConversationStateMachine


class _FakeWakeDetector:
    """Детектор-заглушка: не тянет Vosk и ONNX в юнит-тест."""

    def __init__(self, detect=False):
        self.detect = detect
        self.calls = 0

    def process_pcm(self, pcm: bytes) -> bool:
        self.calls += 1
        return self.detect


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


def _loud_frame(value=4000, samples=512):
    return np.full(samples, value, dtype=np.int16).tobytes()


def test_gateway_open_switches_standby_to_listening():
    """Открытие окна активности переводит машину в LISTENING."""
    sm, pipe = _pipeline(lambda: True, rms_threshold=100.0)
    sent = []
    pipe.on_speech_frame = sent.append

    pipe._process_frame_in_worker(_loud_frame(), b"", time.monotonic(), sm.state, True)

    assert sm.state == ConversationState.LISTENING
    assert sent, "при открытом шлюзе речь обязана уходить в облако"


def test_gateway_close_returns_machine_to_standby():
    """Закрытие окна активности обязано вернуть машину в STANDBY.

    Регрессия: обратной связи не было вовсе. Машина заходила в LISTENING и
    оставалась там навсегда — ключевое слово переставало требоваться, а весь
    микрофон уходил в Gemini до перезапуска процесса.
    """
    gate = {"open": True}
    sm, pipe = _pipeline(lambda: gate["open"], rms_threshold=100.0)
    sent = []
    pipe.on_speech_frame = sent.append

    pipe._process_frame_in_worker(_loud_frame(), b"", time.monotonic(), sm.state, True)
    assert sm.state == ConversationState.LISTENING

    gate["open"] = False
    sent.clear()
    pipe._process_frame_in_worker(_loud_frame(), b"", time.monotonic(), sm.state, False)

    assert sm.state == ConversationState.STANDBY, "шлюз закрыт, а машина осталась слушать"
    # Сразу после закрытия шлюза в облако ещё ~1 с досылается ЦИФРОВАЯ тишина —
    # чтобы VAD Gemini закрыл ход; комната туда не уходит никогда.
    assert all(set(f) == {0} for f in sent), "при закрытом шлюзе в облако не должна уходить комната"
    assert pipe.wake_detector.calls > 0, "в STANDBY обязан работать детектор ключевого слова"


def test_push_frame_does_not_move_state_machine():
    """Аудиоколбэк обязан быть без побочных эффектов.

    Переход состояния тянет за собой слушателей — UI и COM-дакинг pycaw.
    Их вызов внутри realtime-колбэка PortAudio даёт щелчки и потерю кадров,
    поэтому push_frame только снимает признаки и кладёт кадр в очередь.
    """
    sm, pipe = _pipeline(lambda: True)
    pipe._running = True
    transitions = []
    sm.subscribe(lambda old, new, reason: transitions.append((old, new)))

    assert pipe.push_frame(_loud_frame()) is True

    assert transitions == [], "push_frame двигает машину состояний прямо в аудиопотоке"
    assert sm.state == ConversationState.STANDBY

    # Признаки момента захвата всё же должны доехать до воркера.
    item = pipe._queue.get_nowait()
    assert item[3] == ConversationState.STANDBY
    assert item[4] is True


def test_barge_in_without_aec_ignores_loud_rms():
    """Без опорного сигнала колонок громкость не является поводом для перебивания.

    Иначе Джарвис перебивает сам себя: собственный голос из динамиков попадает
    в микрофон и превышает порог RMS.
    """
    sm, pipe = _pipeline(lambda: False)
    barge = []
    pipe.on_barge_in = barge.append
    sm.transition_to(ConversationState.LISTENING, reason="test")
    sm.transition_to(ConversationState.SPEAKING, reason="test")

    assert pipe.aec_active is False
    pipe._process_frame_in_worker(
        _loud_frame(20000), b"", time.monotonic() + 1.0, ConversationState.SPEAKING, False
    )
    assert barge == [], "перебивание по RMS без AEC — это самоперебивание"

    # А вот ключевое слово перебивает и без AEC.
    pipe.wake_detector.detect = True
    pipe._process_frame_in_worker(
        _loud_frame(20000), b"", time.monotonic() + 1.0, ConversationState.SPEAKING, False
    )
    assert barge == ["wake-word-barge-in"]


def test_vad_hangover_keeps_word_endings():
    """После громкого кадра «хвост» тихих ещё уезжает в облако.

    Без хвоста обрезаются окончания слов: человек договаривает тише, чем
    начинал, и последний слог не доходит до распознавания.
    """
    hangover = 3
    sm, pipe = _pipeline(lambda: True, rms_threshold=1000.0, hangover_frames=hangover)
    sent = []
    pipe.on_speech_frame = sent.append
    quiet = np.zeros(512, dtype=np.int16).tobytes()

    def feed(frame):
        pipe._process_frame_in_worker(frame, b"", time.monotonic(), sm.state, True)

    feed(_loud_frame(4000))
    assert len(sent) == 1

    for _ in range(hangover):
        feed(quiet)
    assert len(sent) == 1 + hangover, "хвост тишины обязан доехать целиком"

    # За пределами хвоста поток НЕ обрывается — VAD на стороне Gemini работает
    # по непрерывному звуку и на дырках не может ни услышать начало речи, ни
    # закрыть ход. Но уходит цифровая тишина, а не звук комнаты.
    before_tail_cut = len(sent)
    for _ in range(5):
        feed(quiet)
    хвост = sent[before_tail_cut:]
    assert len(хвост) == 5, "поток в облако оборвался — VAD Gemini на дырках слепнет"
    assert all(set(кадр) == {0} for кадр in хвост), "за пределами хвоста ушёл звук комнаты"


def test_speech_captured_before_answer_is_not_dropped_as_echo():
    """Кадр, захваченный во время LISTENING, доезжает до облака.

    Регрессия: маршрутизация шла по состоянию на момент РАЗБОРА кадра. Пока
    кадр лежал в очереди, приходил ответ модели, состояние становилось
    SPEAKING — и речь пользователя выбрасывалась как эхо.
    """
    sm, pipe = _pipeline(lambda: True, rms_threshold=100.0)
    sent = []
    pipe.on_speech_frame = sent.append

    sm.transition_to(ConversationState.LISTENING, reason="test")
    captured_state = sm.state
    # Пока кадр ждал в очереди, Джарвис начал отвечать.
    sm.transition_to(ConversationState.SPEAKING, reason="answer started")

    pipe._process_frame_in_worker(_loud_frame(), b"", time.monotonic(), captured_state, True)

    assert sent, "речь пользователя выброшена как эхо собственного ответа"


def test_wake_spotted_keeps_listening_even_if_queued_frame_had_gate_closed():
    """Когда wake-word зафиксирован, кадры из очереди не должны сбрасывать LISTENING в STANDBY.

    Регрессия (13:28:59):
    1. Vosk подтверждает 'джервис'.
    2. on_wake открывает шлюз на 8 секунд.
    3. Но в очереди аудиоконвейера находился кадр, захваченный до фиксации wake (gate_open=False).
    4. _resolve_state видел gate_open=False и мгновенно (за 0 мс) переводил LISTENING -> STANDBY (gateway closed).
    5. Джарвис мгновенно засыпал и молчал.
    """
    gate_active = [False]
    sm, pipe = _pipeline(lambda: gate_active[0], rms_threshold=100.0)
    sent = []
    pipe.on_speech_frame = sent.append

    def fake_on_wake():
        gate_active[0] = True

    pipe.on_wake = fake_on_wake

    # Имитируем фиксацию wake-слова
    pipe._handle_wake_spotted(score=1.0)
    assert sm.state == ConversationState.LISTENING
    assert gate_active[0] is True

    # Следом воркер разбирает кадр, захваченный ДО wake-слова (с gate_open=False)
    pipe._process_frame_in_worker(_loud_frame(), b"", time.monotonic(), ConversationState.LISTENING, False)

    assert sm.state == ConversationState.LISTENING, "старый кадр с gate_open=False сбросил LISTENING в STANDBY!"
    assert sent, "речь должна уходить в облако, так как шлюз открыт"



def test_cloud_turn_is_drained_with_silence_after_local_command():
    """Команда исполнена локально → STANDBY; в облако ещё ~1 с идёт тишина,
    чтобы VAD Gemini закрыл ход. Иначе он дослушивал оборванный ход со
    следующим «Джарвис» и исполнял старую команду второй раз (стенд 16.09.2026)."""
    import time
    from unittest.mock import MagicMock
    from core.audio_pipeline import AudioPipeline, CLOUD_TURN_DRAIN_SEC
    from core.conversation_state import ConversationState, ConversationStateMachine

    sent = []
    sm = ConversationStateMachine()
    pipe = AudioPipeline(
        state_machine=sm, gateway_active_provider=lambda: False,
        on_speech_frame=sent.append, enable_aec=False, enable_ducking=False,
        enable_endpointing=False, enable_local_stt=False,
    )
    pipe.wake_detector = MagicMock(process_pcm=MagicMock(return_value=False))
    pipe.frames_sent_since_wake = 40  # реплика ушла в облако
    t = time.monotonic()
    frame = b"\x01\x00" * 512

    for i in range(int(CLOUD_TURN_DRAIN_SEC / 0.032) + 5):
        pipe._process_frame_in_worker(frame, b"", t + i * 0.032, ConversationState.STANDBY, False)

    drained = [f for f in sent if set(f) == {0}]
    assert len(drained) >= int(CLOUD_TURN_DRAIN_SEC / 0.032) - 1, "тишина в облако не дослана"
    assert len(sent) == len(drained), "в STANDBY в облако ушёл не только дренаж"
    assert pipe.frames_sent_since_wake == 0, "после дренажа счётчик обнулён — второй раз не дренируем"
