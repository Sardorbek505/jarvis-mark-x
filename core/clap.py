"""Два хлопка → интро Джарвиса. Офлайн, без моделей: по форме звука.

Хлопок — короткий (затухает за ~0,1 с), резкий (громкость прыгает в разы за
10 мс) и широкополосный (много энергии выше 2 кГц). Речь тянется дольше и
сидит ниже, клавиатура тише и стучит очередью. Поэтому «двойной хлопок» —
ровно два таких удара с паузой 0,12–0,9 с, и тишина до первого и после
второго: очередь стуков или аплодисменты интро не включают.

Интерфейс как у детекторов слова: feed(pcm 16 кГц int16) из потока
микрофона, разбор в своём потоке, on_double() при срабатывании.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

RATE = 16000
FRAME = RATE // 100                  # 10 мс
# Порог — от фона комнаты, а не числом: у тихого микрофона (ASUS с ИИ-шумодавом
# голос RMS ~200–600) хлопок тоже тихий. Фон — медленная огибающая тихих кадров.
FLOOR_X = 10.0                       # хлопок громче фона во столько раз
MIN_RMS = 60.0                      # и не тише этого (цифровая тишина)
JUMP = 4.0                           # во столько раз громче предыдущих 30 мс
HF_SHARE = 0.35                      # доля энергии выше 2 кГц
DECAY_FRAMES = 12                    # через 120 мс должно затихнуть…
DECAY_TO = 0.3                       # …до 30 % пика
GAP = (0.12, 0.9)                    # пауза между хлопками, с
QUIET = 0.6                          # тишина до первого и после второго, с
COOLDOWN = 3.0


def _hf_share(x: np.ndarray) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    total = float(spec.sum())
    if total <= 0:
        return 0.0
    cut = int(2000 / (RATE / 2) * (len(spec) - 1))
    return float(spec[cut:].sum()) / total


class ClapCounter:
    """Логика без потоков (её и проверяют тесты): кадры по 10 мс → момент двойного хлопка."""

    TAIL = 0.25                        # хвост хлопка (эхо комнаты) — не «другой звук»

    def __init__(self):
        self.t = 0.0                   # время по звуку, с
        self._buf = np.zeros(0, dtype=np.float64)
        self._hist = [0.0, 0.0, 0.0]
        self._cand: tuple[float, float, int] | None = None   # (время, пик, кадров после)
        self.claps: list[float] = []   # подтверждённые хлопки (время начала)
        self.other: list[float] = []   # прочие громкие звуки
        self._cool_until = 0.0
        self.floor = 100.0             # фон комнаты (RMS)

    def push(self, pcm: bytes) -> list[float]:
        """Кадры в разбор; возвращает моменты подтверждённых двойных хлопков."""
        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64)
        self._buf = np.concatenate((self._buf, x))
        out = []
        while len(self._buf) >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            self._frame(frame)
            self.t += FRAME / RATE
            hit = self._decide()
            if hit is not None:
                out.append(hit)
        return out

    def _frame(self, f: np.ndarray):
        rms = float(np.sqrt(np.mean(f * f)))
        before = max(self._hist)
        self._hist = self._hist[1:] + [rms]
        if rms < self.floor * 3:                      # фон: вниз быстро, вверх медленно
            self.floor += (0.05 if rms < self.floor else 0.005) * (rms - self.floor)
        loud = max(MIN_RMS, FLOOR_X * self.floor)
        if self._cand is not None:
            t0, peak, n = self._cand
            peak, n = max(peak, rms), n + 1
            self._cand = (t0, peak, n)
            if n >= DECAY_FRAMES:
                self._cand = None
                (self.claps if rms < DECAY_TO * peak else self.other).append(t0)
            return
        if rms > loud and rms > JUMP * max(before, 1.0) and _hf_share(f) > HF_SHARE:
            self._cand = (self.t, rms, 0)
        elif rms > loud * 0.5 and not any(0 <= self.t - c <= self.TAIL for c in self.claps):
            self.other.append(self.t)

    def _decide(self) -> float | None:
        """Ровно два хлопка с нужной паузой и тишина вокруг — решаем, когда тишина после прошла."""
        horizon = self.t - (GAP[1] + 2 * QUIET + 1.0)
        self.claps = [c for c in self.claps if c > horizon]
        self.other = [o for o in self.other if o > horizon]
        if len(self.claps) < 2 or self._cand is not None:
            return None
        first, second = self.claps[-2], self.claps[-1]
        if self.t - second < QUIET:
            return None                           # ждём: вдруг будет третий
        lo, hi = first - QUIET, second + QUIET
        ok = (GAP[0] <= second - first <= GAP[1]
              and sum(lo <= c <= hi for c in self.claps) == 2
              and not any(lo <= o <= hi for o in self.other)
              and first >= self._cool_until)
        self.claps = []
        if not ok:
            return None
        self._cool_until = self.t + COOLDOWN
        return second


class ClapDetector:
    def __init__(self, on_double: Callable[[], None]):
        self._on_double = on_double
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self.counter = ClapCounter()

    def start(self) -> bool:
        threading.Thread(target=self._run, daemon=True, name="clap").start()
        return True

    def stop(self):
        self._stop.set()

    def feed(self, pcm: bytes):
        try:
            self._q.put_nowait(pcm)
        except queue.Full:
            pass

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    pcm = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if self.counter.push(pcm):
                    logger.info("Два хлопка — интро")
                    try:
                        self._on_double()
                    except Exception as exc:
                        logger.warning("Обработчик хлопков: %s", exc)
        except Exception:
            logger.exception("Детектор хлопков упал")


def wait_clear(det: ClapDetector, timeout: float = 2.0):
    """Для тестов: дождаться, пока поток разберёт очередь."""
    end = time.monotonic() + timeout
    while not det._q.empty() and time.monotonic() < end:
        time.sleep(0.01)
