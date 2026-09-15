"""Tests for wake responsiveness and silence fix."""
import asyncio
import numpy as np
import pytest
from core.wakeword import generate_chime_pcm
from core.aec_pipeline import AECPipeline
from core.headless_ui import HeadlessUI
from main import Jarvis
from google.genai import types

@pytest.fixture
def jarvis():
    ui = HeadlessUI()
    j = Jarvis(ui=ui)
    try:
        yield j
    finally:
        j.cleanup()

def test_chime_pcm_generation():
    pcm = generate_chime_pcm(24000)
    assert len(pcm) > 0
    arr = np.frombuffer(pcm, dtype=np.int16)
    assert len(arr) > 1000
    assert int(np.max(np.abs(arr))) > 10000

def test_aec_no_false_divergence_on_low_power():
    aec = AECPipeline()
    mic = (np.random.randn(512) * 5).astype(np.int16).tobytes()
    ref = (np.random.randn(512) * 5).astype(np.int16).tobytes()
    clean, erle = aec.process_frame(mic, ref)
    assert len(clean) == len(mic)

@pytest.mark.asyncio
async def test_on_speech_frame_not_zeroed(jarvis):
    jarvis.out_queue = asyncio.Queue(maxsize=50)
    jarvis._loop = asyncio.get_running_loop()
    jarvis._on_wake_spotted()

    test_frame = b"\x12\x34" * 256
    jarvis._on_speech_frame(test_frame)
    await asyncio.sleep(0.05)

    assert not jarvis.out_queue.empty()
    item = jarvis.out_queue.get_nowait()
    assert item["data"] == test_frame

def test_build_config_has_wake_protocol_and_high_sensitivity(jarvis):
    cfg = jarvis._build_config()
    assert "обратился к тебе только по имени" in cfg.system_instruction
    assert cfg.realtime_input_config.automatic_activity_detection.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_HIGH
