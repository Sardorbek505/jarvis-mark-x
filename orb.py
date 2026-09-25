"""Шар из точек в центре HUD — геометрия без Qt.

Точки лежат на сфере равномерно (спираль Фибоначчи). Поверхность «дышит»:
радиус каждой точки сдвигается суммой нескольких медленных волн, бегущих по
разным направлениям. В тишине волны едва заметны, от голоса вспухают лопастями.

Плавность держится на трёх правилах:
- всё считается от реального времени (dt), а не от номера кадра: пропущенный
  кадр не дёргает шар, а просто показывает его чуть дальше;
- фаза волн и поворот НАКАПЛИВАЮТСЯ. Если бы скорость умножалась на общее
  время, её смена (тишина → речь) мгновенно перескакивала бы в другую точку
  волны — шар бы дёрнулся;
- громкость сглаживается: вверх быстро, вниз медленно, как стрелка прибора.
"""
from __future__ import annotations

import math

import numpy as np

_GOLDEN = math.pi * (3.0 - math.sqrt(5.0))


def fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n, dtype=np.float64)
    y = 1.0 - 2.0 * (i + 0.5) / n
    r = np.sqrt(1.0 - y * y)
    theta = _GOLDEN * i
    return np.stack([np.cos(theta) * r, y, np.sin(theta) * r], axis=1)


class DotOrb:
    # Волны: (направление задаётся случайно с фиксированным зерном, чтобы шар
    # всегда выглядел одинаково), пространственная частота, скорость, вес.
    _WAVES = ((2.1, 0.9, 1.0), (2.9, -1.3, 0.8), (3.6, 1.7, 0.55), (1.6, -0.7, 0.9))

    def __init__(self, n: int = 2400, seed: int = 7):
        self.points = fibonacci_sphere(n)
        rng = np.random.default_rng(seed)
        dirs = rng.normal(size=(len(self._WAVES), 3))
        self._dirs = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)
        self._freq = np.array([w[0] for w in self._WAVES])
        self._speed = np.array([w[1] for w in self._WAVES])
        self._weight = np.array([w[2] for w in self._WAVES])
        self._weight /= self._weight.sum()
        self._offset = rng.uniform(0, 2 * math.pi, size=len(self._WAVES))
        # Проекции точек на направления волн — не меняются, считаем один раз.
        self._proj = self.points @ self._dirs.T * self._freq      # (n, k)

        self.energy = 0.0      # сглаженная громкость 0..1
        self._phase = 0.0      # накопленная фаза волн
        self._yaw = 0.0        # накопленный поворот

    def step(self, dt: float, level: float, active: bool = False):
        """Продвинуть анимацию на dt секунд. level — сырая громкость 0..1."""
        dt = max(0.0, min(dt, 0.1))          # после зависания не прыгаем на секунды
        level = max(0.0, min(1.0, level))
        # Подъём ~150 мс, спад ~350 мс: быстрее — шар вздрагивает на каждом
        # слове (замерено: при 14/с точка прыгала на 5% радиуса за кадр).
        rate = 6.5 if level > self.energy else 2.8
        self.energy += (level - self.energy) * (1.0 - math.exp(-rate * dt))
        # Волны бегут быстрее, когда шар говорит или работает, но разгоняются
        # через накопленную фазу — без скачка.
        self._phase += dt * (0.55 + (0.5 if active else 0.0) + 2.2 * self.energy)
        self._yaw += dt * (0.16 + 0.25 * self.energy)

    def project(self, cx: float, cy: float, radius: float, idle_amp: float = 0.045):
        """Экранные x, y и глубина z (-1 дальняя сторона … +1 ближняя)."""
        amp = idle_amp + 0.30 * self.energy
        waves = np.sin(self._proj + self._phase * self._speed + self._offset)
        r = 1.0 + amp * (waves @ self._weight)
        pts = self.points * r[:, None]

        cy_, sy_ = math.cos(self._yaw), math.sin(self._yaw)
        x = pts[:, 0] * cy_ + pts[:, 2] * sy_
        z = -pts[:, 0] * sy_ + pts[:, 2] * cy_
        y = pts[:, 1]
        tilt = 0.32                         # лёгкий наклон — видно «макушку»
        ct, st = math.cos(tilt), math.sin(tilt)
        y, z = y * ct - z * st, y * st + z * ct

        persp = 3.6 / (3.6 - z)             # мягкая перспектива
        sx = cx + x * radius * persp
        sy = cy - y * radius * persp
        return sx, sy, z
