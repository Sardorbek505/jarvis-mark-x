"""Нейросетевой эндпоинтинг и предзагрузка синтеза.

Замеры, из которых это выросло (эта машина, живое железо):
  * фон микрофона в тихой комнате: медиана RMS 86, p90 = 201, p99 = 456 —
    энергетический порог здесь принципиально не отделяет речь от шума;
  * Silero VAD: 0.38 мс на кадр 64 мс, модель уже лежит на диске (её кладёт
    openwakeword), скачивать нечего;
  * тёплый запрос синтеза к Fish: ~690 мс и почти не зависит от длины текста —
    значит резать фразы мельче бессмысленно, а вот запустить запрос раньше
    выгодно ровно на это время.
"""

import asyncio
import time
from unittest.mock import patch

import numpy as np
import pytest

from core.endpointing import SileroEndpointer
from core.audio_pipeline import AudioPipeline
from core.conversation_state import ConversationState, ConversationStateMachine


# ─── Эндпоинтер ───────────────────────────────────────────────────────────────
def _speechlike(n: int, sr: int = 16000, seed: int = 0) -> bytes:
    """Сигнал с формантной структурой — грубая модель голоса, не белый шум."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    sig = np.zeros(n)
    for f, a in ((120, 0.5), (350, 0.35), (900, 0.2), (2400, 0.1)):
        sig += a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4.5 * t)      # слоговая огибающая
    return np.clip(sig * env * 9000, -32768, 32767).astype(np.int16).tobytes()


def _silence(n: int) -> bytes:
    return np.zeros(n, dtype=np.int16).tobytes()


def _room_noise(n: int, rms: float = 90.0, seed: int = 1) -> bytes:
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(0, rms, n), -32768, 32767).astype(np.int16).tobytes()


@pytest.fixture
def endpointer():
    ep = SileroEndpointer(hangover_ms=200, min_speech_frames=1)
    if not ep.available:
        pytest.skip("silero_vad.onnx недоступен в этом окружении")
    return ep


def test_silence_is_not_speech(endpointer):
    """Тишина не должна считаться речью ни на одном кадре."""
    probs = [endpointer.speech_probability(_silence(1024)) for _ in range(10)]
    assert max(probs) < 0.5, f"тишина принята за речь: max={max(probs):.2f}"


def test_room_noise_is_not_speech(endpointer):
    """Фоновый шум с RMS 90 — тот самый, что энергетический порог пропускал.

    Ради этого всё и затевалось: RMS 90 против порога 12 проходил как речь,
    и комната непрерывно уезжала в облако.
    """
    probs = [endpointer.speech_probability(_room_noise(1024, 90.0, seed=i)) for i in range(15)]
    assert max(probs) < 0.5, f"шум комнаты принят за речь: max={max(probs):.2f}"


def test_turn_end_fires_once_after_speech(endpointer):
    """Конец фразы отмечается ровно один раз и только после речи."""
    ends = 0
    for i in range(12):
        _, ended = endpointer.process(_speechlike(1024, seed=i), 64.0)
        ends += int(ended)
    assert ends == 0, "конец фразы объявлен посреди речи"

    for _ in range(10):
        _, ended = endpointer.process(_silence(1024), 64.0)
        ends += int(ended)
    assert ends == 1, f"конец фразы отмечен {ends} раз вместо одного"


def test_short_pause_does_not_split_the_phrase(endpointer):
    """Пауза между словами короче hangover не должна рвать фразу."""
    for i in range(6):
        endpointer.process(_speechlike(1024, seed=i), 64.0)
    assert endpointer.in_speech

    # 128 мс паузы при hangover 200 мс
    for _ in range(2):
        _, ended = endpointer.process(_silence(1024), 64.0)
        assert not ended, "фраза разорвана на обычной паузе между словами"
    assert endpointer.in_speech, "фраза прервана раньше времени"


def test_reset_clears_recurrent_state(endpointer):
    """Между ходами состояние сети не должно течь."""
    for i in range(6):
        endpointer.process(_speechlike(1024, seed=i), 64.0)
    assert endpointer.in_speech
    endpointer.reset()
    assert not endpointer.in_speech
    assert endpointer.last_probability == 0.0


# ─── Конвейер использует эндпоинтер ───────────────────────────────────────────
def test_pipeline_prefers_silero_over_energy_threshold():
    """Конвейер должен брать нейросетевой эндпоинтинг, а не порог громкости."""
    pipe = AudioPipeline(
        state_machine=ConversationStateMachine(),
        gateway_active_provider=lambda: False,
        enable_aec=False,
        enable_ducking=False,
    )
    try:
        if pipe.endpointer is None:
            pytest.skip("silero_vad.onnx недоступен в этом окружении")
        assert pipe.get_stats()["endpointing"] == "silero"
    finally:
        pipe.stop()


def test_pipeline_falls_back_honestly_without_model():
    """Без модели тракт обязан честно откатиться на порог, а не притворяться."""
    pipe = AudioPipeline(
        state_machine=ConversationStateMachine(),
        gateway_active_provider=lambda: False,
        enable_aec=False,
        enable_ducking=False,
        enable_endpointing=False,
    )
    try:
        assert pipe.endpointer is None
        assert pipe.get_stats()["endpointing"] == "rms"
    finally:
        pipe.stop()


def test_room_noise_does_not_reach_the_cloud_with_endpointing():
    """Сквозная проверка: с эндпоинтингом шум комнаты в облако не уходит."""
    sm = ConversationStateMachine()
    sent = []
    pipe = AudioPipeline(
        state_machine=sm,
        gateway_active_provider=lambda: True,
        on_speech_frame=sent.append,
        enable_aec=False,
        enable_ducking=False,
        rms_threshold=12.0,
        hangover_frames=2,
    )
    try:
        if pipe.endpointer is None:
            pytest.skip("silero_vad.onnx недоступен в этом окружении")
        t = time.monotonic()
        for i in range(20):
            pipe._process_frame_in_worker(
                _room_noise(1024, 90.0, seed=i), b"", t + i * 0.064,
                ConversationState.LISTENING, True,
            )
        # Поток непрерывен: кадров уходит столько же, сколько пришло. Но всё,
        # что не признано речью, заменено цифровой тишиной — звук комнаты
        # в облако не попадает.
        assert len(sent) == 20, "поток в облако оборвался"
        шумовые = sent[pipe.hangover_frames:]
        assert шумовые, "нечего проверять — хвост съел весь прогон"
        assert all(set(кадр) == {0} for кадр in шумовые), (
            "шум комнаты уехал в облако при пороге RMS 12"
        )
    finally:
        pipe.stop()


def test_wake_word_is_vetoed_when_there_was_no_speech():
    """Ключевое слово отклоняется, если речи в комнате не было.

    Грамматика Vosk состоит из ~25 слов, и декодер обязан выдать лучшую
    гипотезу на любой вход — бытовой шум превращается в «джарвис» или «дальше».
    Нейросетевой VAD знает, была ли речь вообще, и это единственный дешёвый
    способ отличить настоящее обращение от щелчка двери.
    """
    sm = ConversationStateMachine()
    woken = []
    pipe = AudioPipeline(
        state_machine=sm,
        gateway_active_provider=lambda: False,
        on_wake=lambda: woken.append(True),
        enable_aec=False,
        enable_ducking=False,
    )
    try:
        if pipe.endpointer is None:
            pytest.skip("silero_vad.onnx недоступен в этом окружении")

        # В комнате был только шум — окно вероятностей заполняется тишиной.
        t = time.monotonic()
        for i in range(10):
            pipe._process_frame_in_worker(
                _room_noise(1024, 90.0, seed=i), b"", t + i * 0.064,
                ConversationState.STANDBY, False,
            )
        pipe._handle_wake_spotted(0.9)
        assert woken == [], "ключевое слово принято на шуме без единого кадра речи"
        assert pipe.wake_vetoed_count == 1

        # А после настоящей речи то же срабатывание проходит.
        for i in range(10):
            pipe._process_frame_in_worker(
                _speechlike(1024, seed=i), b"", t + (10 + i) * 0.064,
                ConversationState.STANDBY, False,
            )
        pipe._handle_wake_spotted(0.9)
        assert woken == [True], "после речи ключевое слово обязано срабатывать"
    finally:
        pipe.stop()


def test_wake_veto_is_disabled_without_endpointer():
    """Без модели вето накладывать не на чем — детектор работает как раньше."""
    sm = ConversationStateMachine()
    woken = []
    pipe = AudioPipeline(
        state_machine=sm,
        gateway_active_provider=lambda: False,
        on_wake=lambda: woken.append(True),
        enable_aec=False,
        enable_ducking=False,
        enable_endpointing=False,
    )
    try:
        pipe._handle_wake_spotted(0.9)
        assert woken == [True], "без эндпоинтера вето блокировать ничего не должно"
        assert pipe.wake_vetoed_count == 0
    finally:
        pipe.stop()


# ─── Предзагрузка синтеза ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_speech_prefetch_starts_before_turn_completes():
    """Запрос синтеза первой фразы уходит до конца генерации ответа."""
    import main as jarvis_main

    class _Stub:
        _speech_prefetch = None
        _loop = None

    j = _Stub()
    j._loop = asyncio.get_running_loop()
    j._maybe_prefetch_speech = jarvis_main.Jarvis._maybe_prefetch_speech.__get__(j)
    j._take_prefetched_speech = jarvis_main.Jarvis._take_prefetched_speech.__get__(j)

    async def _fake_speak(text, sample_rate=24000):
        return b"\x01\x02" * 100

    with patch.object(jarvis_main, "get_voice_provider", lambda: "fish"), \
         patch("telegram_bot.tts_fish.is_configured", lambda: True), \
         patch("telegram_bot.tts_fish.speak_pcm", _fake_speak):

        # Пока фраза одна, она ещё может дорасти — предзагрузки быть не должно.
        j._maybe_prefetch_speech("Разумеется")
        assert j._speech_prefetch is None

        # Появилась вторая фраза — первая окончательна, запрос уходит.
        j._maybe_prefetch_speech("Разумеется, сэр. Включаю музыку и приглушаю фон.")
        assert j._speech_prefetch is not None, "синтез не стартовал заранее"

        fragment = j._speech_prefetch[0]
        task = j._take_prefetched_speech(fragment)
        assert task is not None, "готовый синтез не отдан плееру"
        assert await task == b"\x01\x02" * 100
        assert j._speech_prefetch is None, "предзагрузка не снята после выдачи"


@pytest.mark.asyncio
async def test_prefetch_is_not_reused_for_a_different_phrase():
    """Чужой текст не должен получить заранее синтезированный звук."""
    import main as jarvis_main

    class _Stub:
        _speech_prefetch = None

    j = _Stub()
    j._take_prefetched_speech = jarvis_main.Jarvis._take_prefetched_speech.__get__(j)

    task = asyncio.get_running_loop().create_future()
    task.set_result(b"pcm-first-phrase")
    j._speech_prefetch = ("Разумеется, сэр.", task)

    assert j._take_prefetched_speech("Совсем другая фраза.") is None
    assert j._speech_prefetch is not None, "предзагрузка потеряна из-за чужого текста"
    assert j._take_prefetched_speech("Разумеется, сэр.") is task
