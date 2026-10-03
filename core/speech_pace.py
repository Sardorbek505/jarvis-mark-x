"""Темп речи Джарвиса: без провалов тишины между предложениями.

Ответ озвучивается по предложениям (пока звучит одно — готовится следующее),
и каждый кусок от синтеза (Fish, Edge) приходит со своей тишиной в начале и в
конце. На стыке они складывались: после каждой точки Джарвис молчал заметную
долю секунды, будто задумался. Внутри куска синтез тоже иногда тянет паузу на
запятой или двоеточии.

Tightener срезает тишину по краям куска (оставляя мягкое начало и хвост
согласной), сжимает слишком длинные паузы внутри до естественной длины, а
между кусками кладёт свою короткую паузу — чуть длиннее после конца
предложения, чем после запятой. Быстро и чётко, но не скороговоркой.

Работает на лету, кадрами по 10 мс: звук Fish приходит потоком, и ждать
весь кусок ради обрезки тишины — значит потерять выигрыш потока.
"""
from __future__ import annotations

import collections
import os

import numpy as np

FRAME_SEC = 0.01
KEEP_LEAD_SEC = 0.02           # мягкое начало первого звука
KEEP_TAIL_SEC = 0.06           # хвост последней согласной («с», «т») тихий — не отрезать
MAX_INNER_PAUSE_SEC = float(os.getenv("JARVIS_MAX_PAUSE_MS", "280")) / 1000
SENTENCE_PAUSE_SEC = float(os.getenv("JARVIS_SENTENCE_PAUSE_MS", "150")) / 1000
CLAUSE_PAUSE_SEC = 0.06
START_RMS = 80.0               # первый звук куска (int16)
QUIET_RMS = 150.0              # тише — пауза (≈ −47 дБ)


class Tightener:
    """Поток int16 mono PCM → тот же звук без лишней тишины. feed() — по мере
    прихода, finish() — в конце куска (хвост + своя пауза)."""

    def __init__(self, rate: int):
        self.rate = rate
        self.n = max(1, int(rate * FRAME_SEC))
        self.started = False
        self._carry = np.zeros(0, dtype=np.int16)
        self._lead: collections.deque = collections.deque(maxlen=max(1, round(KEEP_LEAD_SEC / FRAME_SEC)))
        self._pending: list[np.ndarray] = []           # тишина после последнего звука
        self._keep = round(MAX_INNER_PAUSE_SEC / FRAME_SEC)
        self.dropped = 0                               # сколько сэмплов тишины выброшено
        self._odd = b""                                # половинка сэмпла с прошлого куска

    def feed(self, pcm: bytes) -> bytes:
        pcm = self._odd + pcm
        cut = len(pcm) // 2 * 2
        self._odd = pcm[cut:]
        x = np.frombuffer(pcm[:cut], dtype=np.int16)
        data = np.concatenate([self._carry, x]) if len(self._carry) else x
        k = len(data) // self.n
        self._carry = data[k * self.n:].copy()
        out: list[np.ndarray] = []
        for i in range(k):
            f = data[i * self.n:(i + 1) * self.n]
            r = float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))
            if not self.started:
                if r >= START_RMS:
                    self.started = True
                    out.extend(self._lead)
                    out.append(f)
                    self._lead.clear()
                else:
                    if len(self._lead) == self._lead.maxlen:
                        self.dropped += self.n
                    self._lead.append(f)
            elif r < QUIET_RMS:
                self._pending.append(f)
            else:
                out.extend(self._release())
                out.append(f)
        return np.concatenate(out).tobytes() if out else b""

    def _release(self) -> list[np.ndarray]:
        """Пауза кончилась звуком: короткую — как есть, длинную — сжать до MAX."""
        p, self._pending = self._pending, []
        if len(p) <= self._keep:
            return p
        half = self._keep // 2
        self.dropped += (len(p) - self._keep) * self.n
        return p[:half] + p[len(p) - (self._keep - half):]

    def finish(self, sentence_end: bool = True) -> bytes:
        if not self.started:
            return b""
        tail_frames = round(KEEP_TAIL_SEC / FRAME_SEC)
        # кусок кончился звуком — недобитый последний кадр (<10 мс) тоже звук
        parts = list(self._pending[:tail_frames]) if self._pending else [self._carry]
        self.dropped += max(0, len(self._pending) - tail_frames) * self.n
        self._pending, self._carry = [], np.zeros(0, dtype=np.int16)
        pause = SENTENCE_PAUSE_SEC if sentence_end else CLAUSE_PAUSE_SEC
        parts.append(np.zeros(int(self.rate * pause), dtype=np.int16))
        return np.concatenate(parts).tobytes()


def tighten(pcm: bytes, rate: int, sentence_end: bool = True) -> bytes:
    """Целый кусок: int16 mono PCM → без тишины по краям и длинных пауз + короткая пауза после."""
    if not pcm or len(pcm) < 4:
        return pcm
    t = Tightener(rate)
    body = t.feed(pcm)
    if not t.started:                                  # сплошная тишина — не трогаем
        return pcm
    return body + t.finish(sentence_end)


def ends_sentence(text: str) -> bool:
    return (text or "").rstrip().endswith((".", "!", "?", "…"))
