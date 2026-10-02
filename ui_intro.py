"""Интро Джарвиса на весь экран (два хлопка). Сценарий и звук — core/intro.py.

Текста на экране нет — только иконки и анимация: полоса-сканер вместо
заголовка, иконки в узлах и в проверке систем, в финале — лицо Джарвиса.

Кадр рисуется функцией от времени (paint_at): окно и превью-видео рисуют
одно и то же, а тест может проверить любой момент без таймеров.
Клик или Esc — пропустить.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QApplication, QWidget

from core import intro as S
from orb import DotOrb
from ui_face import Face
from ui_icons import draw_icon

TEAL = QColor("#3fd0bd")
TEAL_HI = QColor("#8ff3e6")
GOLD = QColor("#ffb547")
GREEN = QColor("#46e880")
WHITE = QColor("#f4fbff")
DIM = QColor("#6f8ca0")


def _c(base: QColor, a: float) -> QColor:
    c = QColor(base)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ease_out(x: float) -> float:
    x = _clamp(x)
    return 1 - (1 - x) ** 3


def _back(x: float) -> float:
    x = _clamp(x)
    s = 1.7
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def _icon(p: QPainter, name: str, c: QPointF, s: float, color: QColor):
    """Иконки ui_icons плюс две своих — солнце (погода) и календарь."""
    if name == "sun":
        p.save()
        p.setPen(QPen(color, max(1.2, s * 0.11), cap=Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, s * 0.2, s * 0.2)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * s * 0.32, c.y() + math.sin(a) * s * 0.32),
                       QPointF(c.x() + math.cos(a) * s * 0.44, c.y() + math.sin(a) * s * 0.44))
        p.restore()
    elif name == "calendar":
        p.save()
        p.setPen(QPen(color, max(1.2, s * 0.11), cap=Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        box = QRectF(c.x() - s * 0.36, c.y() - s * 0.3, s * 0.72, s * 0.66)
        p.drawRoundedRect(box, s * 0.08, s * 0.08)
        p.drawLine(QPointF(box.left(), box.top() + s * 0.2), QPointF(box.right(), box.top() + s * 0.2))
        for dx in (-0.18, 0.18):
            p.drawLine(QPointF(c.x() + s * dx, box.top() - s * 0.1), QPointF(c.x() + s * dx, box.top() + s * 0.06))
        p.restore()
    else:
        draw_icon(p, name, c, s, color)


class IntroScene:
    """Состояние между кадрами — только шар (у него своя физика)."""

    def __init__(self, checks: dict[str, bool] | None = None):
        self.checks = checks or {}
        self.orb = DotOrb(n=900, seed=11)
        self.orb.set_shape("sphere")
        self.face = Face(seed=3)
        self.fade, self.end = S.T_FADE, S.T_END   # продлевается под длину фразы (extend_to)
        self._greeted = False
        self._last_t = 0.0

    def paint_at(self, p: QPainter, w: int, h: int, t: float):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dt, self._last_t = max(0.0, t - self._last_t), t
        fade_out = 1 - _clamp((t - self.fade) / (self.end - self.fade))
        dark = _ease_out(t / 0.5) * fade_out
        p.fillRect(0, 0, w, h, _c(QColor("#02050a"), 0.94 * dark))
        if fade_out <= 0:
            return
        p.setOpacity(fade_out)
        cx, cy = w / 2, h * 0.5
        r = min(w, h) * 0.15

        self._sweep(p, w, h, t)
        self._scanner(p, w, h, t)
        self._orbits(p, cx, cy, r, t)
        self._nodes(p, cx, cy, r, t)
        self._ring(p, cx, cy, r, t)
        self._sphere(p, cx, cy, r, t, dt)
        self._checks(p, w, h, t)
        self._final(p, w, h, cx, cy, r, t)
        p.setOpacity(1.0)

    # ── части кадра ───────────────────────────────────────────────────────────
    def _sweep(self, p, w, h, t):
        if 0.05 < t < 0.7:                                    # луч «сканирования» сверху вниз
            y = h * _ease_out((t - 0.05) / 0.65)
            g = QRadialGradient(QPointF(w / 2, y), w * 0.6)
            g.setColorAt(0, _c(TEAL, 0.35))
            g.setColorAt(1, _c(TEAL, 0))
            p.fillRect(QRectF(0, y - 1.5, w, 3), g)

    def _scanner(self, p, w, h, t):
        """Вместо «СИСТЕМНАЯ ПРОВЕРКА» — полоса из делений: по одному на тик звука,
        вдоль неё бежит блик. Уходит, когда появляются узлы."""
        if t < S.T_TYPE or t > S.T_NODES + 0.3:
            return
        a = 1 - _clamp((t - S.T_NODES) / 0.3)
        times = S.type_times()
        n = len(times)
        bw, y = w * 0.26, h * 0.12
        x0 = (w - bw) / 2
        gap = bw / n
        seg = gap * 0.62
        p.setPen(Qt.PenStyle.NoPen)
        for i, at in enumerate(times):
            on = _clamp((t - at) / 0.08)
            p.setBrush(_c(TEAL_HI if on >= 1 else TEAL, a * (0.18 + 0.82 * on)))
            p.drawRoundedRect(QRectF(x0 + i * gap, y - 3, seg, 6), 2, 2)
        done = sum(1 for at in times if at <= t)
        if 0 < done < n or (t < S.T_RING + 0.6):
            u = (t * 0.9) % 1.0
            gx = x0 + bw * u
            g = QRadialGradient(QPointF(gx, y), gap * 2.2)
            g.setColorAt(0, _c(WHITE, 0.6 * a))
            g.setColorAt(1, _c(WHITE, 0))
            p.setBrush(g)
            p.drawEllipse(QPointF(gx, y), gap * 2.2, gap * 2.2)

    def _ring(self, p, cx, cy, r, t):
        if t < S.T_RING:
            return
        prog = _ease_out((t - S.T_RING) / (S.T_HIT - S.T_RING))
        pulse = 1 + 0.10 * math.exp(-max(0, t - S.T_HIT) / 0.25) * (t >= S.T_HIT)
        pulse += 0.06 * math.exp(-max(0, t - S.T_FINAL) / 0.3) * (t >= S.T_FINAL)
        rr = r * pulse
        rect = QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr)
        span = int(-360 * 16 * prog)
        for width, alpha in ((r * 0.30, 0.06), (r * 0.16, 0.12), (r * 0.07, 0.35), (r * 0.025, 1.0)):
            p.setPen(QPen(_c(GOLD, alpha), width, cap=Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, span)
        if t >= S.T_HIT:                                     # вспышка удара
            fl = math.exp(-(t - S.T_HIT) / 0.18)
            g = QRadialGradient(QPointF(cx, cy), r * 2.2)
            g.setColorAt(0, _c(WHITE, 0.55 * fl))
            g.setColorAt(1, _c(TEAL, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(cx, cy), r * 2.2, r * 2.2)

    def _sphere(self, p, cx, cy, r, t, dt):
        if t < S.T_HIT:
            return
        a = _ease_out((t - S.T_HIT) / 0.6) * (1 - _ease_out((t - S.T_FINAL) / 0.35))
        if a <= 0:
            return
        self.orb.step(min(dt, 0.05), level=0.25 + 0.6 * math.exp(-(t - S.T_HIT) / 0.5), active=True)
        xs, ys, zs = self.orb.project(cx, cy, r * 0.78)
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, z in zip(xs, ys, zs):
            depth = (z + 1) / 2
            p.setBrush(_c(TEAL_HI if depth > 0.6 else TEAL, a * (0.25 + 0.75 * depth)))
            d = 1.0 + 1.6 * depth
            p.drawEllipse(QPointF(x, y), d, d)

    def _orbits(self, p, cx, cy, r, t):
        if t < S.T_HIT:
            return
        a = _ease_out((t - S.T_HIT) / 0.8)
        for k, (scale, speed) in enumerate(((1.28, 25), (1.5, -16), (1.75, 9))):
            pen = QPen(_c(TEAL, 0.35 * a / (k + 1)), 1.2)
            pen.setDashPattern([2, 6 + 3 * k])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = r * scale
            p.save()
            p.translate(cx, cy)
            p.rotate(speed * (t - S.T_HIT))
            p.drawEllipse(QPointF(0, 0), rr, rr)
            p.restore()

    def node_pos(self, i: int, cx: float, cy: float, r: float) -> tuple[float, float]:
        ang = -math.pi / 2 + 2 * math.pi * i / len(S.NODES)
        return cx + math.cos(ang) * r * 2.75, cy + math.sin(ang) * r * 2.05

    def _nodes(self, p, cx, cy, r, t):
        f = QFont("Segoe UI", max(9, int(r * 0.13)), QFont.Weight.DemiBold)
        p.setFont(f)
        for i, (name, at) in enumerate(zip(S.NODES, S.node_times())):
            if t < at:
                continue
            nx, ny = self.node_pos(i, cx, cy, r)
            ang = math.atan2(ny - cy, nx - cx)
            sx, sy = cx + math.cos(ang) * r * 1.05, cy + math.sin(ang) * r * 1.05
            grow = _ease_out((t - at) / 0.35)
            ex, ey = sx + (nx - sx) * grow, sy + (ny - sy) * grow
            p.setPen(QPen(_c(TEAL, 0.55), 1.3))
            p.drawLine(QPointF(sx, sy), QPointF(ex, ey))
            # импульсы бегут по линии от кольца к узлу
            for k in range(2):
                u = ((t - at) * 0.9 + k * 0.5) % 1.0
                if grow >= 1:
                    px, py = sx + (nx - sx) * u, sy + (ny - sy) * u
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(_c(TEAL_HI, 0.8 * (1 - u)))
                    p.drawEllipse(QPointF(px, py), 2.2, 2.2)
            if grow < 1:
                continue
            pop = _back((t - at - 0.3) / 0.3)
            rad = r * 0.17 * pop
            g = QRadialGradient(QPointF(nx, ny), rad * 2.2)
            g.setColorAt(0, _c(TEAL, 0.40))
            g.setColorAt(1, _c(TEAL, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(nx, ny), rad * 2.2, rad * 2.2)
            p.setBrush(_c(QColor("#04121a"), 0.95))
            p.setPen(QPen(_c(TEAL_HI, 0.9), 1.6))
            p.drawEllipse(QPointF(nx, ny), rad, rad)
            if pop > 0.05:
                _icon(p, S.NODE_ICONS.get(name, "spark"), QPointF(nx, ny), rad * 1.05,
                      _c(TEAL_HI, _clamp((t - at - 0.35) / 0.25)))

    def _checks(self, p, w, h, t):
        """Проверка систем — столбик иконок: серые, по очереди загораются зелёным
        с галочкой (или красным с крестиком, если не работает)."""
        if t < S.T_CHECKS - 0.25:
            return
        size = h * 0.05
        x, y0, step = w * 0.06, h * 0.5 - (len(S.CHECKS) - 1) * size * 0.75, size * 1.5
        for i, (name, at) in enumerate(zip(S.CHECKS, S.check_times())):
            appear = _clamp((t - at + 0.25) / 0.2)
            if appear <= 0:
                continue
            c = QPointF(x, y0 + i * step)
            done = t >= at
            ok = self.checks.get(name, True)
            col = (GREEN if ok else QColor("#ff4660")) if done else DIM
            pop = 1 + 0.25 * math.exp(-max(0, t - at) / 0.12) * done
            rr = size * 0.5 * pop
            p.setBrush(_c(QColor("#04121a"), 0.9 * appear))
            p.setPen(QPen(_c(col, appear), 1.6))
            p.drawEllipse(c, rr, rr)
            _icon(p, S.CHECK_ICONS.get(name, "spark"), c, rr * 1.05, _c(col, appear))
            if done:                                       # значок-итог в углу
                b = QPointF(c.x() + rr * 0.75, c.y() + rr * 0.75)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawEllipse(b, rr * 0.36, rr * 0.36)
                draw_icon(p, "check" if ok else "close", b, rr * 0.42, QColor("#02050a"))

    def _final(self, p, w, h, cx, cy, r, t):
        """Вместо «ДЖАРВИС — ОНЛАЙН»: шар внутри кольца собирается в лицо, оно машет."""
        if t < S.T_FINAL:
            return
        if not self._greeted:
            self.face.greet()
            self.face.set_emotion("happy")
            self._greeted = True
        target = t - S.T_FINAL                   # догоняем время мелкими шагами: кадры могут пропадать
        while self.face.clock < target - 1e-6:
            self.face.step(min(0.05, target - self.face.clock))
        a = _ease_out((t - S.T_FINAL) / 0.35)
        p.save()
        p.setOpacity(p.opacity() * a)
        self.face.paint(p, cx, cy, r * 0.9)
        p.restore()

    def extend_to(self, end: float):
        """Джарвис ещё говорит — лицо остаётся, картинка тает к end."""
        if end > self.end:
            self.end, self.fade = end, end - (S.T_END - S.T_FADE)

    def render(self, t: float, w: int = 1280, h: int = 720) -> QImage:
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor("#0b1a26"))                          # «рабочий стол» под затемнением
        p = QPainter(img)
        self.paint_at(p, w, h, t)
        p.end()
        return img


class IntroOverlay(QWidget):
    """Окно поверх всего экрана. play() — показать; finished — интро кончилось или его пропустили."""

    finished = pyqtSignal()

    def __init__(self, checks: dict[str, bool] | None = None, clock=time.monotonic):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.scene = IntroScene(checks)
        self._clock = clock
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.skipped = False

    def play(self):
        scr = QApplication.primaryScreen()
        if scr is not None:
            self.setGeometry(scr.geometry())
        self._t0 = self._clock()
        self.show()
        self.raise_()
        self._timer.start()

    def elapsed(self) -> float:
        return self._clock() - self._t0

    def _tick(self):
        if self.elapsed() >= self.scene.end:
            self._done()
        else:
            self.update()

    def _done(self):
        if self._timer.isActive():
            self._timer.stop()
            self.finished.emit()
            self.close()

    def skip(self):
        self.skipped = True
        self._done()

    def mousePressEvent(self, e):
        self.skip()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.skip()

    def paintEvent(self, e):
        p = QPainter(self)
        self.scene.paint_at(p, self.width(), self.height(), self.elapsed())
        p.end()
