"""Покадровая съёмка настоящих окон Джарвиса (Qt offscreen) в виртуальном времени.

Все анимации Джарвиса считают шаг от time.monotonic(). Здесь часы подменяются:
каждый кадр двигает время ровно на 1/fps, поэтому запись плавная (30 fps)
при любой скорости захвата. Отрисовку делает сам Джарвис — мы только снимаем.
"""
from __future__ import annotations

import os
import shutil
import sys
import time as _time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
JV = os.environ.get("JV_ROOT", "")            # копия репо с демо-данными (seed.py)
if JV:
    sys.path.insert(0, JV)
    os.chdir(JV)

from PyQt6.QtCore import QEventLoop  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
OUT = os.environ.get("REC_OUT", "promo/build/clips")
FPS = 30

_real_mono, _real_time = _time.monotonic, _time.time


class _VClock:
    def __init__(self):
        self.off, self.on = 0.0, False
        self.base_m, self.base_t = _real_mono(), _real_time()

    def mono(self):
        return self.base_m + self.off

    def wall(self):
        return self.base_t + self.off


V = _VClock()


def vstart():
    if not V.on:
        V.base_m, V.base_t, V.off, V.on = _real_mono(), _real_time(), 0.0, True
        _time.monotonic, _time.time = V.mono, V.wall


def _settle():
    end = _real_mono() + 0.020                 # дать QTimer(16 мс) сработать хотя бы раз
    while _real_mono() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)


def vpump(seconds: float):
    vstart()
    for _ in range(int(seconds * FPS)):
        V.off += 1 / FPS
        _settle()


def vrecord(name: str, widget, seconds: float, events=()):
    """events: [(секунда клипа, вызов)]. Ровно FPS кадров на секунду анимации."""
    vstart()
    d = os.path.join(OUT, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    evs = sorted(events, key=lambda e: e[0])
    i, t0 = 0, V.off
    n = int(round(seconds * FPS))
    for k in range(n):
        t = k / FPS
        while i < len(evs) and evs[i][0] <= t + 1e-9:
            evs[i][1]()
            i += 1
        V.off = t0 + t
        _settle()
        widget.grab().save(os.path.join(d, f"{k:05d}.png"))
    print(f"{name}: {n} frames", flush=True)
