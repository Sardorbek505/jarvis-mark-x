"""Шар из точек: плавность и привязка к реальному времени."""
import numpy as np

from orb import DotOrb, fibonacci_sphere


def _max_jump(orb, level_fn, frames=240, dt=1 / 60):
    prev = None
    worst = 0.0
    for i in range(frames):
        orb.step(dt, level_fn(i))
        x, y, _ = orb.project(0.0, 0.0, 1.0)
        cur = np.column_stack((x, y))
        if prev is not None:
            worst = max(worst, float(np.abs(cur - prev).max()))
        prev = cur
    return worst


def test_points_lie_on_unit_sphere():
    pts = fibonacci_sphere(500)
    assert np.allclose(np.linalg.norm(pts, axis=1), 1.0)


def test_no_jerk_when_voice_starts_and_stops():
    # Громкость скачет тишина ↔ крик каждые полсекунды — шар не должен
    # прыгать: за кадр точка сдвигается не больше чем на 3% радиуса.
    orb = DotOrb(800)
    worst = _max_jump(orb, lambda i: 1.0 if (i // 30) % 2 else 0.0)
    assert worst < 0.03


def test_animation_follows_time_not_frames():
    # 30 fps и 60 fps за одну секунду приходят в одно и то же состояние.
    a, b = DotOrb(300), DotOrb(300)
    for _ in range(60):
        a.step(1 / 60, 0.5)
    for _ in range(30):
        b.step(1 / 30, 0.5)
    xa, ya, _ = a.project(0, 0, 1)
    xb, yb, _ = b.project(0, 0, 1)
    assert np.abs(xa - xb).max() < 0.02 and np.abs(ya - yb).max() < 0.02


def test_long_freeze_does_not_teleport():
    orb = DotOrb(300)
    orb.step(1 / 60, 0.0)
    x0, _, _ = orb.project(0, 0, 1)
    orb.step(5.0, 0.0)                    # окно висело 5 секунд
    x1, _, _ = orb.project(0, 0, 1)
    assert np.abs(x1 - x0).max() < 0.05


def _run(orb, seconds, level=0.3, dt=1 / 60):
    prev, worst = None, 0.0
    for _ in range(int(seconds / dt)):
        orb.step(dt, level)
        x, y, _ = orb.project(0.0, 0.0, 1.0)
        cur = np.column_stack((x, y))
        if prev is not None:
            worst = max(worst, float(np.abs(cur - prev).max()))
        prev = cur
    return worst


def test_morph_is_continuous_and_returns_to_sphere():
    orb = DotOrb(900)
    _run(orb, 0.5)
    for shape in ("globe", "music", "film", "screen", "sphere"):
        orb.set_shape(shape)
        # точки летят, но не телепортируются: за кадр < 12% радиуса
        assert _run(orb, 1.6) < 0.12, shape
    radii = np.linalg.norm(orb.pos, axis=1)
    assert 0.8 < radii.min() and radii.max() < 1.3       # снова шар


def test_flat_shapes_face_the_viewer_whatever_the_spin():
    # Экран обязан стоять прямоугольником лицом к зрителю при любом повороте.
    orb = DotOrb(900)
    orb.set_shape("screen")
    for _ in range(3):
        _run(orb, 1.5)
        x, y, _ = orb.project(0.0, 0.0, 1.0)
        assert abs(x.max() - 0.95) < 0.03 and abs(x.min() + 0.95) < 0.03
        assert abs(y.max() - 0.535) < 0.03 and abs(y.min() + 0.535) < 0.03


def test_unknown_shape_is_ignored():
    orb = DotOrb(300)
    orb.set_shape("dragon")
    assert orb.shape == "sphere"
