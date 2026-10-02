"""Два хлопка → интро (core/clap.py): ловим пару хлопков, не ловим речь, печать и аплодисменты."""
import time

import numpy as np
import pytest

from core import clap as C

R = C.RATE
rng = np.random.default_rng(5)


def _clap(amp=12000.0, dec=0.03):
    n = int(0.25 * R)
    t = np.arange(n) / R
    x = rng.normal(0, 1, n) * np.exp(-t / dec)
    x = np.convolve(x, [1, -0.6])[:n]
    x += 0.15 * rng.normal(0, 1, n) * np.exp(-t / 0.12)
    return x / np.max(np.abs(x)) * amp


def _place(total, events, noise=60.0):
    y = rng.normal(0, noise, int(total * R))
    for at, sig in events:
        i = int(at * R)
        y[i:i + len(sig)] += sig[: len(y) - i]
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()


def _hits(pcm):
    c = C.ClapCounter()
    out = []
    for i in range(0, len(pcm), 2048):
        out += c.push(pcm[i:i + 2048])
    return out + c.push(b"\0\0" * R)


@pytest.mark.parametrize("gap", [0.15, 0.3, 0.5, 0.8])
@pytest.mark.parametrize("amp", [4000, 15000, 28000])
def test_double_clap_is_heard(gap, amp):
    assert len(_hits(_place(3, [(0.8, _clap(amp)), (0.8 + gap, _clap(amp))]))) == 1


def test_quiet_microphone_hears_quiet_claps():
    """ASUS с ИИ-шумодавом: всё в 10 раз тише — порог от фона, не числом."""
    assert len(_hits(_place(3, [(0.8, _clap(1500)), (1.2, _clap(1500))], noise=6))) == 1


def test_one_clap_is_not_two():
    assert _hits(_place(3, [(1.0, _clap())])) == []


@pytest.mark.parametrize("gap", [0.3, 0.5])
def test_three_claps_are_not_two(gap):
    assert _hits(_place(4, [(0.8, _clap()), (0.8 + gap, _clap()), (0.8 + 2 * gap, _clap())])) == []


def test_applause_and_typing_do_not_trigger():
    applause = [(0.5 + i * 0.13 + rng.uniform(0, 0.05), _clap(rng.uniform(5000, 15000))) for i in range(25)]
    assert _hits(_place(5, applause)) == []
    taps = np.cumsum(rng.uniform(0.07, 0.35, 40))
    assert _hits(_place(taps[-1] + 1, [(t, _clap(rng.uniform(1500, 6000), 0.008)) for t in taps])) == []


def test_sustained_sound_is_not_a_clap():
    """Речь и музыка тянутся — хлопок затихает за 0,1 с."""
    t = np.arange(int(0.6 * R)) / R
    vowel = np.sin(2 * np.pi * 220 * t) * 6000 * np.minimum(1, t / 0.005)
    burst = rng.normal(0, 6000, len(t))                        # «ш-ш-ш» — шумно, но долго
    assert _hits(_place(3, [(0.5, vowel), (1.3, vowel)])) == []
    assert _hits(_place(3, [(0.5, burst), (1.3, burst)])) == []


def test_cooldown_between_intros():
    pair = [(0.8, _clap()), (1.1, _clap()), (2.6, _clap()), (2.9, _clap())]
    assert len(_hits(_place(5, pair))) == 1


def test_detector_thread_calls_back():
    heard = []
    d = C.ClapDetector(lambda: heard.append(1))
    d.start()
    pcm = _place(3, [(0.8, _clap()), (1.1, _clap())])
    for i in range(0, len(pcm), 2048):
        d.feed(pcm[i:i + 2048])
    d.feed(b"\0\0" * R)
    C.wait_clear(d)
    time.sleep(0.2)
    d.stop()
    assert heard == [1]


def test_each_clap_is_logged(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger=C.logger.name):
        _hits(_place(3, [(1.0, _clap())]))
    assert "Хлопок" in caplog.text
