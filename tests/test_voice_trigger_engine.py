"""Unit-тесты для VoiceTriggerEngine, AECPipeline, DuckingController и WakeWordDetector2Stage."""

import time
import numpy as np
import pytest

from core.ducking_controller import DuckingController, DuckingState
from core.aec_pipeline import AECPipeline
from core.wake_detector import WakeWordDetector2Stage
from core.voice_trigger_engine import VoiceTriggerEngine


def test_ducking_controller_states():
    """Проверка переходов состояний в DuckingController."""
    dc = DuckingController(duck_ratio=0.2, attack_ms=20.0, release_ms=50.0)
    assert dc.state == DuckingState.IDLE

    # LISTENING (Attack)
    dc.set_state(DuckingState.LISTENING)
    assert dc.state == DuckingState.LISTENING

    # THINKING (Hold)
    dc.set_state(DuckingState.THINKING)
    assert dc.state == DuckingState.THINKING

    # SPEAKING (Hold)
    dc.set_state(DuckingState.SPEAKING)
    assert dc.state == DuckingState.SPEAKING

    # RESTORING (Release)
    dc.set_state(DuckingState.RESTORING)
    assert dc.state == DuckingState.RESTORING

    # Ждём завершения затухания -> IDLE
    time.sleep(0.1)
    assert dc.state == DuckingState.IDLE
    dc.close()


def test_aec_pipeline_processing():
    """Проверка адаптивного эхоподавления AECPipeline."""
    aec = AECPipeline(sample_rate=16000, filter_length=256)
    sr = 16000
    t = np.linspace(0, 0.2, int(sr * 0.2), endpoint=False)

    # Музыка и тихий голос
    music = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    voice = (np.sin(2 * np.pi * 1200 * t) * 2000).astype(np.int16)
    mic = music + voice

    clean_bytes, erle = aec.process_frame(mic.tobytes(), music.tobytes())
    assert len(clean_bytes) == len(mic.tobytes())
    assert erle >= 0.0

    # Проверка обхода при тишине в динамиках
    silence_ref = np.zeros(len(t), dtype=np.int16).tobytes()
    clean_bytes2, erle2 = aec.process_frame(mic.tobytes(), silence_ref)
    assert clean_bytes2 == mic.tobytes()
    assert erle2 == 0.0


def test_wake_detector_silence_rejection():
    """Проверка отсечения тишины двухстадийным детектором."""
    detector = WakeWordDetector2Stage()
    silence = np.zeros(1280, dtype=np.int16).tobytes()
    is_wake = detector.process_pcm(silence)
    assert is_wake is False


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

    # Имитация входящего кадра микрофона и динамиков
    dummy_mic = np.random.randint(-1000, 1000, 1024, dtype=np.int16).tobytes()
    dummy_ref = np.zeros(1024, dtype=np.int16).tobytes()

    vte._running = True
    vte._handle_raw_frame(dummy_mic, dummy_ref, time.perf_counter())

    assert len(clean_frames) == 1
    assert len(clean_frames[0]) == len(dummy_mic)
    vte.stop()
