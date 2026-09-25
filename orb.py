"""Шар из точек в центре HUD — геометрия без Qt.

Точки лежат на сфере равномерно (спираль Фибоначчи). Поверхность «дышит»:
радиус каждой точки сдвигается суммой нескольких медленных волн, бегущих по
разным направлениям. В тишине волны едва заметны, от голоса вспухают лопастями.

Пока Джарвис работает, те же точки перетекают в фигуру по теме команды:
глобус (поиск, браузер, погода), эквалайзер (музыка), киноплёнка (фильм),
экран (посмотри на экран / в камеру) — и обратно в шар. Каждая точка летит
к своему месту с небольшой задержкой, поэтому фигура собирается волной
сверху вниз, а не щёлкает.

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
_TILT = 0.32                     # лёгкий наклон — видно «макушку»
_PERSP = 3.6                     # расстояние до камеры: мягкая перспектива

SHAPES = ("sphere", "globe", "music", "film", "screen", "reactor")


def fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n, dtype=np.float64)
    y = 1.0 - 2.0 * (i + 0.5) / n
    r = np.sqrt(1.0 - y * y)
    theta = _GOLDEN * i
    return np.stack([np.cos(theta) * r, y, np.sin(theta) * r], axis=1)


def _spread(n: int, lengths) -> list[int]:
    """Разделить n точек между линиями пропорционально их длине."""
    lengths = np.asarray(lengths, dtype=np.float64)
    raw = n * lengths / lengths.sum()
    counts = np.floor(raw).astype(int)
    for i in np.argsort(-(raw - counts))[: n - counts.sum()]:
        counts[i] += 1
    return counts.tolist()


def _by_height(pts: np.ndarray) -> np.ndarray:
    """Упорядочить сверху вниз — как точки сферы. Тогда i-я точка шара летит
    к i-й точке фигуры примерно на той же высоте, а не через весь экран."""
    return pts[np.argsort(-pts[:, 1], kind="stable")]


# ── Фигуры: базовая раскладка (один раз) ─────────────────────────────────────

def _globe_base(n: int) -> np.ndarray:
    """Параллели и меридианы — узнаваемый глобус из одних точек."""
    lats = np.radians([-60, -30, 0, 30, 60])
    lons = np.radians(np.arange(0, 180, 30))          # 6 полных меридианов
    lines = [("lat", a, 2 * math.pi * math.cos(a)) for a in lats]
    lines += [("lon", a, 2 * math.pi) for a in lons]
    out = []
    for (kind, a, _), k in zip(lines, _spread(n, [ln for *_, ln in lines])):
        t = np.linspace(0, 2 * math.pi, k, endpoint=False)
        if kind == "lat":
            out.append(np.stack([np.cos(t) * math.cos(a), np.full(k, math.sin(a)),
                                 np.sin(t) * math.cos(a)], axis=1))
        else:
            out.append(np.stack([np.cos(t) * math.cos(a), np.sin(t),
                                 np.cos(t) * math.sin(a)], axis=1))
    return np.concatenate(out)


def _film_base(n: int) -> np.ndarray:
    """Лента киноплёнки, замкнутая в кольцо: края, кадры, перфорация."""
    R = 1.05
    parts = []
    # края ленты и верх/низ кадров — сплошные окружности
    rings = [(0.44, 1.0), (-0.44, 1.0), (0.27, 1.0), (-0.27, 1.0)]
    # перфорация — пунктир ближе к краям
    holes = [(0.355, 0.5), (-0.355, 0.5)]
    frames = 15                                         # вертикальные рамки кадров
    lens = [2 * math.pi * R * w for _, w in rings + holes] + [0.54] * frames
    counts = _spread(n, lens)
    for (y, w), k in zip(rings + holes, counts):
        t = np.linspace(0, 2 * math.pi, k, endpoint=False)
        if w < 1.0:                                     # пунктир: 48 «окошек»
            t = t[(np.floor(t / (2 * math.pi) * 96) % 2) == 0]
            t = np.resize(t, k)
        parts.append(np.stack([np.cos(t) * R, np.full(k, y), np.sin(t) * R], axis=1))
    for f, k in zip(range(frames), counts[len(rings) + len(holes):]):
        a = 2 * math.pi * f / frames
        y = np.linspace(-0.27, 0.27, k)
        parts.append(np.stack([np.full(k, math.cos(a) * R), y,
                               np.full(k, math.sin(a) * R)], axis=1))
    return np.concatenate(parts)


def _reactor_base(n: int) -> np.ndarray:
    """Дуговой реактор Старка в плоскости экрана: (радиус, угол, крутится ли,
    яркость). Ядро, кольца, треугольник и десять катушек по кругу."""
    parts = []

    def ring(r, k, rot=0.0, z=0.0):
        a = np.linspace(0, 2 * math.pi, k, endpoint=False)
        parts.append(np.stack([np.full(k, r), a, np.full(k, rot), np.full(k, z)], 1))

    k_core = int(n * 0.10)
    i = np.arange(k_core)                                   # ядро — диск по спирали
    parts.append(np.stack([0.16 * np.sqrt((i + 0.5) / k_core), i * _GOLDEN,
                           np.zeros(k_core), np.full(k_core, 0.55)], 1))
    ring(0.22, int(n * 0.05), z=0.35)
    # треугольник, вписанный в r=0.40, вершиной вниз
    k_tri = int(n * 0.10)
    corners = [math.pi / 2 + j * 2 * math.pi / 3 + math.pi for j in range(3)]
    pts = []
    for j in range(3):
        a0, a1 = corners[j], corners[(j + 1) % 3]
        t = np.linspace(0, 1, k_tri // 3, endpoint=False)
        x = 0.40 * ((1 - t) * math.cos(a0) + t * math.cos(a1))
        y = 0.40 * ((1 - t) * math.sin(a0) + t * math.sin(a1))
        pts.append(np.stack([np.hypot(x, y), np.arctan2(y, x)], 1))
    tri = np.concatenate(pts)
    parts.append(np.column_stack([tri, np.zeros(len(tri)), np.full(len(tri), 0.3)]))
    ring(0.47, int(n * 0.06))
    ring(0.58, int(n * 0.07))
    # десять катушек: каждая — несколько дуг шириной 22° между r 0.63 и 0.86
    used = sum(len(x) for x in parts)
    k_out = int(n * 0.14)
    k_coil = n - used - k_out
    per_coil = k_coil // 10
    rows = 5
    for c in range(10):
        base = c * 2 * math.pi / 10
        for r_i in range(rows):
            k = per_coil // rows + (1 if r_i < per_coil % rows else 0)
            r = 0.63 + 0.23 * r_i / (rows - 1)
            a = base + np.linspace(-math.radians(11), math.radians(11), k)
            parts.append(np.stack([np.full(k, r), a, np.ones(k), np.full(k, 0.15)], 1))
    used = sum(len(x) for x in parts)
    ring(0.93, (n - used) // 2)
    ring(1.0, n - used - (n - used) // 2)
    return np.concatenate(parts)[:n]


class DotOrb:
    # Волны: пространственная частота, скорость, вес. Направления задаются
    # случайно с фиксированным зерном — шар всегда выглядит одинаково.
    _WAVES = ((2.1, 0.9, 1.0), (2.9, -1.3, 0.8), (3.6, 1.7, 0.55), (1.6, -0.7, 0.9))
    _BARS = 56                   # столбиков в эквалайзере

    def __init__(self, n: int = 2400, seed: int = 7):
        self.points = fibonacci_sphere(n)
        self.n = n
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

        self._globe = _by_height(_globe_base(n))
        self._film = _by_height(_film_base(n))
        # Эквалайзер и экран живут в плоскости экрана — раскладку храним
        # параметрами, а координаты считаем каждый кадр.
        per = n // self._BARS
        idx = np.arange(n)
        self._bar = np.minimum(idx // per, self._BARS - 1)
        self._bar_u = (idx % per) / max(1, per - 1)              # 0 низ … 1 верх
        self._bar_col = np.where(idx % 2 == 0, -1.0, 1.0)
        self._bar_speed = rng.uniform(4.0, 9.0, self._BARS)
        self._bar_phase = rng.uniform(0, 2 * math.pi, self._BARS)
        self._screen = self._screen_base(n)
        rb = _reactor_base(n)
        # сверху вниз, как остальные фигуры, — точки летят на свою высоту
        self._reactor = rb[np.argsort(-(rb[:, 0] * np.sin(rb[:, 1])), kind="stable")]
        # Задержка старта: волна идёт сверху вниз + немного случайности.
        self._delay = 0.35 * (1.0 - self.points[:, 1]) / 2 + rng.uniform(0, 0.12, n)
        self._dur = rng.uniform(0.55, 0.95, n)

        self.energy = 0.0        # сглаженная громкость 0..1
        self._phase = 0.0        # накопленная фаза волн
        self._yaw = 0.0          # накопленный поворот
        self._clock = 0.0
        self.shape = "sphere"
        self._from: np.ndarray | None = None     # откуда летят точки (снимок)
        self._since = 1e9                        # сколько секунд идёт переход
        self.pos = self._target("sphere")

    # ── фигуры: цель на текущий кадр ─────────────────────────────────────────
    @staticmethod
    def _screen_base(n: int) -> np.ndarray:
        w, h = 0.95, 0.535                       # половинки 16:9
        k_border = int(n * 0.38)
        per = np.array([w, h, w, h]) * 2
        pts = []
        for side, k in enumerate(_spread(k_border, per)):
            t = np.linspace(-1, 1, k, endpoint=False)
            if side == 0:
                pts.append(np.stack([t * w, np.full(k, h)], 1))
            elif side == 1:
                pts.append(np.stack([np.full(k, w), -t * h], 1))
            elif side == 2:
                pts.append(np.stack([-t * w, np.full(k, -h)], 1))
            else:
                pts.append(np.stack([np.full(k, -w), t * h], 1))
        rest = n - k_border
        cols = int(math.sqrt(rest * w / h))
        rows = math.ceil(rest / cols)
        gx, gy = np.meshgrid(np.linspace(-w * 0.9, w * 0.9, cols),
                             np.linspace(h * 0.85, -h * 0.85, rows))
        grid = np.stack([gx.ravel(), gy.ravel()], 1)[:rest]
        flat = np.concatenate(pts + [grid])
        return _by_height(np.column_stack([flat, np.zeros(len(flat))]))

    def _unrotate(self, pts: np.ndarray) -> np.ndarray:
        """Координаты «на экране» → мировые: после поворота в project они
        встанут ровно лицом к зрителю, как бы шар ни был повёрнут.

        z тут — только «насколько ближе» (ярче и крупнее). Перспектива от него
        сдвигала бы точку от центра — полоса сканирования вылезала за рамку,
        поэтому x, y заранее поджимаются ровно на столько, сколько растянет
        перспектива."""
        pts = pts.copy()
        pts[:, :2] *= ((_PERSP - pts[:, 2]) / _PERSP)[:, None]
        ct, st = math.cos(_TILT), math.sin(_TILT)
        x, y, z = pts[:, 0], pts[:, 1] * ct + pts[:, 2] * st, -pts[:, 1] * st + pts[:, 2] * ct
        c, s = math.cos(self._yaw), math.sin(self._yaw)
        return np.stack([x * c - z * s, y, x * s + z * c], axis=1)

    def _target(self, shape: str) -> np.ndarray:
        e = self.energy
        if shape == "globe":
            return self._globe * (1.0 + 0.04 * e)
        if shape == "film":
            return self._film * (1.0 + 0.03 * e)
        if shape == "music":
            # Работает и без голоса: столбики прыгают сами, голос их раскачивает.
            drive = 0.35 + 0.65 * e
            t = self._clock
            a = 2 * math.pi * self._bar / self._BARS
            wave = 0.5 + 0.5 * np.sin(t * self._bar_speed[self._bar] + self._bar_phase[self._bar])
            swell = 0.65 + 0.35 * np.sin(2 * a + t * 0.9)
            height = 0.08 + 0.62 * drive * wave * swell
            r = 0.52 + self._bar_u * height
            tang = self._bar_col * 0.011
            x = np.cos(a) * r - np.sin(a) * tang
            y = np.sin(a) * r + np.cos(a) * tang
            z = 0.5 * self._bar_u * height        # верхушки ближе — ярче и крупнее
            return self._unrotate(np.stack([x, y, z], 1))
        if shape == "reactor":
            r, a, rot, z = self._reactor.T
            a = a + rot * self._clock * 0.45           # катушки медленно вращаются
            pulse = 1.0 + (0.05 + 0.10 * e) * (r < 0.25) * math.sin(self._clock * 4.0)
            r = r * pulse                               # ядро пульсирует
            pts = np.stack([np.cos(a) * r, np.sin(a) * r, z * (0.6 + 0.8 * e)], 1)
            return self._unrotate(pts)
        if shape == "screen":
            pts = self._screen.copy()
            scan = 0.535 - (self._clock * 0.55) % 1.07
            pts[:, 2] = 0.6 * np.exp(-((pts[:, 1] - scan) / 0.05) ** 2)
            return self._unrotate(pts)
        amp = 0.045 + 0.30 * e
        waves = np.sin(self._proj + self._phase * self._speed + self._offset)
        return self.points * (1.0 + amp * (waves @ self._weight))[:, None]

    # ── управление ───────────────────────────────────────────────────────────
    def set_shape(self, shape: str):
        if shape not in SHAPES or shape == self.shape:
            return
        self._from = self.pos.copy()     # летим оттуда, где точки сейчас
        self.shape = shape
        self._since = 0.0

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
        spin = {"globe": 0.35, "film": 0.55}.get(self.shape, 0.0)
        self._yaw += dt * (0.16 + 0.25 * self.energy + spin)
        self._clock += dt
        self._since += dt

        target = self._target(self.shape)
        if self._from is None:
            self.pos = target
            return
        k = np.clip((self._since - self._delay) / self._dur, 0.0, 1.0)
        k = k * k * (3.0 - 2.0 * k)          # smoothstep: мягкий старт и посадка
        self.pos = self._from + (target - self._from) * k[:, None]
        if k.min() >= 1.0:
            self._from = None

    def project(self, cx: float, cy: float, radius: float):
        """Экранные x, y и глубина z (-1 дальняя сторона … +1 ближняя)."""
        pts = self.pos
        c, s = math.cos(self._yaw), math.sin(self._yaw)
        x = pts[:, 0] * c + pts[:, 2] * s
        z = -pts[:, 0] * s + pts[:, 2] * c
        y = pts[:, 1]
        ct, st = math.cos(_TILT), math.sin(_TILT)
        y, z = y * ct - z * st, y * st + z * ct

        persp = _PERSP / (_PERSP - z)
        sx = cx + x * radius * persp
        sy = cy - y * radius * persp
        return sx, sy, z
