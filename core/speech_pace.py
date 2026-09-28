"""Темп речи Джарвиса: без провалов тишины между предложениями.

Ответ озвучивается по предложениям (пока звучит одно — готовится следующее),
и каждый кусок от синтеза (Fish, Edge) приходит со своей тишиной в начале и в
конце. На стыке они складывались: после каждой точки Джарвис молчал заметную
долю секунды, будто задумался. Внутри куска синтез тоже иногда тянет паузу на
запятой или двоеточии.

tighten() срезает тишину по краям куска (оставляя мягкое начало и хвост
согласной), сжимает слишком длинные паузы внутри до естественной длины, а
между кусками кладёт свою короткую паузу — чуть длиннее после конца
предложения, чем после запятой. Быстро и чётко, но не скороговоркой.
"""
from __future__ import annotations

import os

import numpy as np

FRAME_SEC = 0.01
KEEP_LEAD_SEC = 0.02           # мягкое начало первого звука
KEEP_TAIL_SEC = 0.06           # хвост последней согласной («с», «т») тихий — не отрезать
MAX_INNER_PAUSE_SEC = float(os.getenv("JARVIS_MAX_PAUSE_MS", "280")) / 1000
SENTENCE_PAUSE_SEC = float(os.getenv("JARVIS_SENTENCE_PAUSE_MS", "150")) / 1000
CLAUSE_PAUSE_SEC = 0.06


def _frames_rms(x: np.ndarray, n: int) -> np.ndarray:
    k = len(x) // n
    if k == 0:
        return np.zeros(0)
    f = x[:k * n].astype(np.float32).reshape(k, n)
    return np.sqrt(np.mean(f * f, axis=1))


def tighten(pcm: bytes, rate: int, sentence_end: bool = True) -> bytes:
    """int16 mono PCM → без тишины по краям и длинных пауз внутри + короткая пауза после."""
    if not pcm or len(pcm) < 4:
        return pcm
    x = np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype=np.int16)
    n = max(1, int(rate * FRAME_SEC))
    rms = _frames_rms(x, n)
    if not len(rms) or rms.max() < 50:                     # сплошная тишина — не трогаем
        return pcm
    peak = float(rms.max())
    edge = rms > max(60.0, peak * 0.015)                   # по краям — чуткий порог
    loud = np.flatnonzero(edge)
    first, last = int(loud[0]), int(loud[-1])
    start = max(0, first * n - int(rate * KEEP_LEAD_SEC))
    end = min(len(x), (last + 1) * n + int(rate * KEEP_TAIL_SEC))

    # внутри: паузы длиннее MAX_INNER_PAUSE_SEC сжимаем (вырезаем середину тишины)
    quiet = rms < max(120.0, peak * 0.03)
    keep = int(MAX_INNER_PAUSE_SEC / FRAME_SEC)
    parts, pos, i = [], start, first
    while i <= last:
        if quiet[i]:
            j = i
            while j <= last and quiet[j]:
                j += 1
            if j - i > keep:
                cut_from = (i + keep // 2) * n
                cut_to = (j - (keep - keep // 2)) * n
                parts.append(x[pos:cut_from])
                pos = cut_to
            i = j
        else:
            i += 1
    parts.append(x[pos:end])
    pause = SENTENCE_PAUSE_SEC if sentence_end else CLAUSE_PAUSE_SEC
    parts.append(np.zeros(int(rate * pause), dtype=np.int16))
    return np.concatenate(parts).tobytes()


def ends_sentence(text: str) -> bool:
    return (text or "").rstrip().endswith((".", "!", "?", "…"))
