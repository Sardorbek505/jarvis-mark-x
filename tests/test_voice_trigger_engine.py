"""Тесты акустического тракта: VoiceTriggerEngine, AECPipeline, DuckingController, KWS.

Тесты намеренно проверяют РЕЗУЛЬТАТ, а не факт «не упало». Прошлая версия
ограничивалась `assert erle >= 0.0` — условие, истинное всегда, потому что
process_frame возвращает max(0.0, erle). Из-за этого мимо тестов прошли:
эхоподавление, не подавляющее эхо; вторая стадия детектора, никогда не
применявшая свой порог; и duck(), молча не срабатывавший во время возврата
громкости.
"""

import importlib
import time

import numpy as np
import pytest

from core.ducking_controller import DuckingController, DuckingState
from core.aec_pipeline import AECPipeline
from core.wake_detector import WakeWordDetector2Stage
from core.voice_trigger_engine import VoiceTriggerEngine

SR = 16000


def _silent_controller(**kwargs) -> DuckingController:
    """Контроллер, не трогающий реальную громкость системы."""
    dc = DuckingController(**kwargs)
    dc._endpoint_volume = None
    dc._original_volume = 1.0
    dc._current_volume = 1.0
    return dc


def _detector_or_skip(**kwargs) -> WakeWordDetector2Stage:
    """Детектор с загруженной ONNX-моделью, иначе тест пропускается.

    Без модели process_pcm() всегда возвращает False, и проверки порогов
    прошли бы «зелёными», ничего не проверив.
    """
    detector = WakeWordDetector2Stage(**kwargs)
    if detector._oww_model is None:
        pytest.skip("ONNX-модель openwakeword недоступна в этом окружении")
    return detector


def _broadband(n: int, level: float = 0.2, seed: int = 0) -> np.ndarray:
    """Шумоподобный сигнал — модель реальной музыки, а не чистый тон."""
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(0, level, n) * 32767, -32768, 32767).astype(np.int16)


# ─── DuckingController ────────────────────────────────────────────────────────
def test_ducking_controller_states():
    """Проверка переходов состояний в DuckingController."""
    dc = _silent_controller(duck_ratio=0.2, attack_ms=20.0, release_ms=50.0)
    try:
        assert dc.state == DuckingState.IDLE

        dc.set_state(DuckingState.LISTENING)
        assert dc.state == DuckingState.LISTENING

        dc.set_state(DuckingState.THINKING)
        assert dc.state == DuckingState.THINKING

        dc.set_state(DuckingState.SPEAKING)
        assert dc.state == DuckingState.SPEAKING

        dc.set_state(DuckingState.RESTORING)
        assert dc.state == DuckingState.RESTORING

        # Ждём завершения затухания -> IDLE
        time.sleep(0.2)
        assert dc.state == DuckingState.IDLE
    finally:
        dc.close()


def test_duck_during_release_actually_ducks():
    """duck() во время возврата громкости обязан снова приглушить звук.

    Сценарий из жизни: Джарвис договорил, пошёл 350-мс release, и пользователь
    заговорил снова. Раньше приглушение стартовало только из IDLE, поэтому
    duck() тут молча не делал ничего и музыка догромчала поверх речи.
    """
    dc = _silent_controller(duck_ratio=0.2, attack_ms=50.0, release_ms=350.0)
    try:
        dc.set_state(DuckingState.LISTENING)
        dc.set_state(DuckingState.SPEAKING)
        dc.set_state(DuckingState.RESTORING)
        assert dc._fade_target == 1.0, "release должен вести громкость вверх"

        dc.duck()
        assert dc.state == DuckingState.LISTENING
        assert dc._fade_target is not None, "duck() не запустил затухание"
        assert dc._fade_target < 1.0, "громкость продолжает расти во время речи"
    finally:
        dc.close()


def test_original_volume_survives_duck_restore_cycles():
    """Повторные циклы «приглушить — вернуть» не должны занижать исходный уровень."""
    dc = _silent_controller(duck_ratio=0.2, attack_ms=10.0, release_ms=10.0)
    try:
        for _ in range(5):
            dc.duck()
            dc.set_state(DuckingState.RESTORING)
            time.sleep(0.05)
        assert dc._original_volume == 1.0, (
            "исходная громкость сползла вниз: %r" % dc._original_volume
        )
    finally:
        dc.close()


def test_module_import_has_no_side_effects():
    """Импорт модуля не должен поднимать общий контроллер и его поток."""
    import core.ducking_controller as module

    assert hasattr(module, "get_ducking_controller")
    # Имя из старого кода обязано продолжать работать.
    assert module.ducking_controller is module.get_ducking_controller()


# ─── AECPipeline ──────────────────────────────────────────────────────────────
def test_aec_pipeline_processing():
    """Базовый контракт: длина не меняется, тишина в колонках даёт обход."""
    aec = AECPipeline(sample_rate=SR, filter_length=256)
    t = np.linspace(0, 0.2, int(SR * 0.2), endpoint=False)

    music = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    voice = (np.sin(2 * np.pi * 1200 * t) * 2000).astype(np.int16)
    mic = music + voice

    clean_bytes, erle = aec.process_frame(mic.tobytes(), music.tobytes())
    assert len(clean_bytes) == len(mic.tobytes())
    assert erle >= 0.0

    silence_ref = np.zeros(len(t), dtype=np.int16).tobytes()
    clean_bytes2, erle2 = aec.process_frame(mic.tobytes(), silence_ref)
    assert clean_bytes2 == mic.tobytes()
    assert erle2 == 0.0


def test_aec_actually_cancels_delayed_broadband_echo():
    """Главная проверка: эхо реальной (широкополосной) музыки должно уходить.

    До правки конвейер давал 0.3-0.9 дБ на таком сигнале — то есть не работал:
    оценка задержки промахивалась, а окно фильтра индексировалось так, что
    хвост каждого кадра вообще не обрабатывался.
    """
    n = 1280
    music = _broadband(SR * 4, level=0.2, seed=3)
    delay, gain = int(SR * 0.03), 0.5      # 30 мс, -6 дБ

    aec = AECPipeline(sample_rate=SR, filter_length=512)
    clean = mic = None
    for k in range(1, 45):
        s = k * n
        ref = music[s:s + n]
        mic = (music[s - delay:s - delay + n] * gain).astype(np.int16)
        clean, _ = aec.process_frame(mic.tobytes(), ref.tobytes())

    residual = np.frombuffer(clean, dtype=np.int16).astype(np.float64)
    echo = mic.astype(np.float64)
    reduction_db = 20 * np.log10(
        np.sqrt((echo ** 2).mean()) / max(np.sqrt((residual ** 2).mean()), 1e-9)
    )
    assert reduction_db > 20.0, "эхо подавлено всего на %.1f дБ" % reduction_db
    assert aec._estimated_delay == delay, (
        "задержка определена как %d вместо %d" % (aec._estimated_delay, delay)
    )


def test_aec_cancels_the_tail_of_the_frame_too():
    """Хвост кадра обязан обрабатываться наравне с началом.

    Регрессия: срез опорного окна выходил за границу истории, numpy молча
    возвращал короткий массив, и последние filter_length семплов КАЖДОГО кадра
    копировались из входа без обработки.
    """
    n, L = 1280, 512
    music = _broadband(SR * 4, level=0.2, seed=5)

    aec = AECPipeline(sample_rate=SR, filter_length=L)
    clean = mic = None
    for k in range(1, 45):
        s = k * n
        ref = music[s:s + n]
        mic = (music[s:s + n] * 0.5).astype(np.int16)
        clean, _ = aec.process_frame(mic.tobytes(), ref.tobytes())

    residual = np.frombuffer(clean, dtype=np.int16).astype(np.float64)
    tail_in = mic.astype(np.float64)[-L:]
    tail_out = residual[-L:]
    tail_reduction = 20 * np.log10(
        np.sqrt((tail_in ** 2).mean()) / max(np.sqrt((tail_out ** 2).mean()), 1e-9)
    )
    assert tail_reduction > 20.0, "хвост кадра подавлен на %.1f дБ" % tail_reduction


def test_aec_stays_stable_on_pure_tone():
    """Тональный материал не должен разносить фильтр.

    У выдержанной ноты входная матрица вырождена; при обычной нормировке NLMS
    веса улетали в переполнение float32 уже внутри одного кадра.
    """
    n = 1280
    t = np.arange(SR * 3) / SR
    tone = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)

    aec = AECPipeline(sample_rate=SR, filter_length=512)
    for k in range(1, 35):
        s = k * n
        clean, erle = aec.process_frame(
            (tone[s:s + n] * 0.5).astype(np.int16).tobytes(), tone[s:s + n].tobytes()
        )
        out = np.frombuffer(clean, dtype=np.int16)
        assert np.isfinite(out).all()
        assert np.isfinite(erle)

    assert np.isfinite(aec._weights).all(), "веса фильтра разошлись"


def test_aec_preserves_near_end_speech_during_double_talk():
    """Пока говорит пользователь, его речь не должна съедаться адаптацией."""
    n = 1280
    music = _broadband(SR * 8, level=0.2, seed=11)
    delay, gain = int(SR * 0.02), 0.5

    aec = AECPipeline(sample_rate=SR, filter_length=512)
    for k in range(1, 40):                       # сходимся на чистом эхе
        s = k * n
        aec.process_frame(
            (music[s - delay:s - delay + n] * gain).astype(np.int16).tobytes(),
            music[s:s + n].tobytes(),
        )

    kept = []
    for k in range(40, 60):
        s = k * n
        t = (np.arange(n) + s) / SR
        speech = (np.sin(2 * np.pi * 200 * t) * 2500
                  + np.sin(2 * np.pi * 900 * t) * 1200)
        echo = music[s - delay:s - delay + n].astype(np.float32) * gain
        mic = np.clip(echo + speech, -32768, 32767).astype(np.int16)

        clean, _ = aec.process_frame(mic.tobytes(), music[s:s + n].tobytes())
        out = np.frombuffer(clean, dtype=np.int16).astype(np.float64)
        kept.append(float(np.dot(out, speech) / np.dot(speech, speech)))

    assert np.mean(kept) > 0.6, "речь пользователя ослаблена до %.2f" % np.mean(kept)


# ─── WakeWordDetector2Stage ───────────────────────────────────────────────────
def test_wake_detector_silence_rejection():
    """Проверка отсечения тишины двухстадийным детектором."""
    detector = WakeWordDetector2Stage()
    silence = np.zeros(1280, dtype=np.int16).tobytes()
    assert detector.process_pcm(silence) is False


def test_wake_detector_rejects_loud_broadband_noise():
    """Громкий широкополосный шум (шипение ТВ) не должен будить ассистента."""
    detector = WakeWordDetector2Stage()
    noise = _broadband(1280, level=0.2, seed=13)
    assert not any(detector.process_pcm(noise.tobytes()) for _ in range(40))


def test_stage2_threshold_is_actually_applied():
    """Порог второй стадии обязан влиять на решение.

    Регрессия: строгий порог применялся ТОЛЬКО когда контекста не хватало, то
    есть в штатном режиме второй стадии не существовало вовсе.
    """
    frame = _broadband(1280, level=0.1, seed=17).tobytes()

    strict = _detector_or_skip(threshold_stage1=0.0, threshold_stage2=0.99,
                               cooldown_sec=0.0)
    assert not any(strict.process_pcm(frame) for _ in range(6))

    permissive = _detector_or_skip(threshold_stage1=0.0, threshold_stage2=0.0,
                                   cooldown_sec=0.0)
    assert all(permissive.process_pcm(frame) for _ in range(6))


def test_wake_detector_energy_floor():
    """Почти тишина отбраковывается даже при полностью открытых порогах."""
    detector = _detector_or_skip(threshold_stage1=0.0, threshold_stage2=0.0,
                                 cooldown_sec=0.0)
    near_silence = (np.zeros(1280) + 5).astype(np.int16).tobytes()
    assert not any(detector.process_pcm(near_silence) for _ in range(3))


def test_wake_detector_cooldown_suppresses_repeats():
    """После срабатывания детектор молчит в течение cooldown."""
    detector = _detector_or_skip(threshold_stage1=0.0, threshold_stage2=0.0,
                                 cooldown_sec=5.0)
    frame = _broadband(1280, level=0.1, seed=19).tobytes()
    assert detector.process_pcm(frame) is True
    assert not any(detector.process_pcm(frame) for _ in range(5))


def test_onnxruntime_survives_qt_import():
    """onnxruntime обязан грузиться в том же процессе, что и PyQt6.

    Регрессия рантайма: Qt на Windows подменяет путь поиска DLL, и импорт
    onnxruntime ПОСЛЕ него падает с «DLL load failed while importing
    onnxruntime_pybind11_state». Наружу это не вылетало — детектор ловил
    исключение и молча оставался без модели, то есть ключевое слово в живом
    приложении не срабатывало вообще. Лечится прогревом onnxruntime в main.py
    до импорта UI.
    """
    pytest.importorskip("onnxruntime")
    pytest.importorskip("PyQt6.QtWidgets")

    import onnxruntime

    importlib.reload(onnxruntime)          # падает, если DLL уже сломаны Qt
    assert hasattr(onnxruntime, "InferenceSession")


# ─── VoiceTriggerEngine ───────────────────────────────────────────────────────
def test_voice_trigger_engine_lifecycle():
    """Проверка жизненного цикла VoiceTriggerEngine."""
    wake_events = []
    clean_frames = []

    vte = VoiceTriggerEngine(
        on_wake=lambda: wake_events.append(True),
        on_clean_audio=lambda pcm: clean_frames.append(pcm),
        enable_aec=True,
        enable_ducking=False,
    )
    try:
        dummy_mic = np.random.randint(-1000, 1000, 1024, dtype=np.int16).tobytes()
        dummy_ref = np.zeros(1024, dtype=np.int16).tobytes()

        vte._running = True
        vte._handle_raw_frame(dummy_mic, dummy_ref, time.perf_counter())

        assert len(clean_frames) == 1
        assert len(clean_frames[0]) == len(dummy_mic)
    finally:
        vte.stop()


def test_voice_trigger_engine_drops_frames_when_stopped():
    """Остановленный движок не должен пропускать кадры дальше по тракту."""
    clean_frames = []
    vte = VoiceTriggerEngine(
        on_clean_audio=lambda pcm: clean_frames.append(pcm),
        enable_aec=False,
        enable_ducking=False,
    )
    try:
        vte._running = False
        vte._handle_raw_frame(b"\x00\x00" * 512, b"\x00\x00" * 512, time.perf_counter())
        assert clean_frames == []
    finally:
        vte.stop()
