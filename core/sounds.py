"""Короткие звуки Джарвиса — подтверждение без слов.

«Готово»: две мягкие ноты вверх (~0,2 с). Звучат вместо «Есть, сэр» после
простой команды: человек слышит, что сделано, и не ждёт фразы.
"""
from __future__ import annotations

import math

import numpy as np


def done_pcm(rate: int = 24000, volume: float = 0.22) -> bytes:
    """PCM int16 моно: две ноты (A5 → E6) с мягкой атакой и затуханием —
    без щелчков в начале и конце."""
    out = []
    for freq, dur, start in ((880.0, 0.085, 0.0), (1318.5, 0.16, 0.07)):
        n = int(rate * dur)
        t = np.arange(n) / rate
        env = np.minimum(1.0, t / 0.006) * np.exp(-t * 18.0)        # 6 мс атака, быстрое затухание
        tone = np.sin(2 * math.pi * freq * t) + 0.25 * np.sin(2 * math.pi * freq * 2 * t)
        out.append((int(rate * start), tone * env))
    total = max(s + len(x) for s, x in out) + int(rate * 0.02)
    buf = np.zeros(total)
    for s, x in out:
        buf[s:s + len(x)] += x
    buf *= volume / max(1e-9, float(np.max(np.abs(buf))))
    return (buf * 32767).astype(np.int16).tobytes()
