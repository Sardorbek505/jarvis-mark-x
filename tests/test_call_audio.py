"""Голос из трубки Telegram → Gemini: чтобы Джарвис слышал, а не «<noise>»."""
import asyncio

import numpy as np
import pytest

from core import tg_call as tc


def tone(rate, sec, hz, amp):
    t = np.arange(int(rate * sec)) / rate
    return (np.sin(2 * np.pi * hz * t) * amp).astype("<i2")


def level(pcm: bytes) -> float:
    x = np.frombuffer(pcm, "<i2").astype(float)
    return float(np.sqrt(np.mean(x * x)))


def downsample(hz, amp=8000):
    d = tc.Downsampler()
    x = tone(48000, 0.5, hz, amp).tobytes()
    return b"".join(d(x[i:i + 960]) for i in range(0, len(x), 960))


def test_speech_band_passes_and_line_hiss_does_not_fold_into_it():
    voice = downsample(1000)
    assert len(voice) == 8000 * 2
    assert level(voice[2000:]) > 0.9 * 8000 / 2 ** 0.5           # речь не теряет громкость
    # 12 кГц после 16 кГц превратились бы в 4 кГц прямо посреди речи.
    # Среднее по тройкам пропускало их на треть; фильтр — почти ничего.
    hiss = downsample(12000)
    assert level(hiss[2000:]) < 0.02 * 8000


def test_quiet_line_is_brought_up_to_speech_level():
    g = tc.LineGain()
    quiet = tone(16000, 1.0, 300, 900).tobytes()                  # тихий голос в трубке
    out = b"".join(g(quiet[i:i + 320]) for i in range(0, len(quiet), 320))
    assert level(out[-6400:]) > 3 * level(quiet[-6400:])
    assert np.abs(np.frombuffer(out, "<i2")).max() < 32767         # без перегруза


def test_line_noise_alone_is_not_amplified():
    g = tc.LineGain()
    rng = np.random.default_rng(1)
    noise = (rng.normal(0, 40, 16000)).astype("<i2").tobytes()     # шипение линии без речи
    out = b"".join(g(noise[i:i + 320]) for i in range(0, len(noise), 320))
    assert level(out) < 1.1 * level(noise)


def test_loud_voice_is_left_alone():
    g = tc.LineGain()
    loud = tone(16000, 0.5, 300, 9000).tobytes()
    out = b"".join(g(loud[i:i + 320]) for i in range(0, len(loud), 320))
    assert out == loud


class _Live:
    def __init__(self):
        self.sent: list[int] = []

    async def send_realtime_input(self, audio):
        self.sent.append(len(audio.data))


@pytest.mark.asyncio
async def test_phone_frames_go_to_gemini_in_batches_not_100_a_second():
    s = tc.CallSession(tg=None, live=None, peer=1, prompt="")
    s._mic = asyncio.Queue()
    live = _Live()
    pump = asyncio.create_task(s._pump_mic(live))
    frame = tone(48000, 0.01, 300, 6000).tobytes()
    for _ in range(50):                                            # 0,5 с голоса по 10 мс
        s._on_audio(frame)
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)
    pump.cancel()
    assert sum(live.sent) == 16000 * 2 * 0.5                       # ничего не потеряно
    assert len(live.sent) <= 25, f"{len(live.sent)} отправок на 50 кадров"


def _talk(s, seconds):
    s._mic = asyncio.Queue()
    frame = tone(48000, 0.01, 300, 900).tobytes()
    for _ in range(int(seconds * 100)):
        s._on_audio(frame)


def test_owner_call_voice_is_saved_for_diagnosis(tmp_path):
    """«Говорю, а он не слышит»: что пришло из трубки и что ушло в Gemini — в файлы."""
    import wave
    s = tc.CallSession(tg=None, live=None, peer=1, prompt="")           # звонок хозяину
    _talk(s, tc.REC_SEC + 5)
    raw, sent = s.save_recording(tmp_path)
    with wave.open(raw) as w:
        assert w.getframerate() == 48000 and w.getnframes() == tc.REC_SEC * 48000   # не больше REC_SEC
    with wave.open(sent) as w:
        assert w.getframerate() == 16000 and w.getnframes() == tc.REC_SEC * 16000
        loud = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
    assert np.sqrt(np.mean(loud[-16000:] ** 2)) > 3 * 900 / 2 ** 0.5          # усиленный — как слышит Gemini


def test_contact_call_is_never_recorded(tmp_path):
    s = tc.CallSession(tg=None, live=None, peer=1, prompt="", callee="Азиз")
    _talk(s, 2)
    assert s.save_recording(tmp_path) == [] and not list(tmp_path.iterdir())
