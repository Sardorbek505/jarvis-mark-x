"""Лицо Джарвиса в капсуле — маленький «камешек» с глазами и эмоциями.

Светлое скруглённое тельце, два тёмных глаза-капсулы, за ним — мягкое
свечение цвета состояния (видно издалека: зелёное — слушает, оранжевое —
говорит). Эмоции:

  calm    — ждёт: моргает раз в несколько секунд, следит глазами за курсором;
  listen  — глаза шире, тельце покачивается;
  think   — смотрит вверх-вбок, рядом «…»;
  talk    — рот открывается от громкости голоса;
  happy   — ^ ^ и подпрыгивает (задача выполнена);
  alert   — круглые глаза и янтарный «!» (нужно разрешение);
  dizzy   — глаза-спирали, покачивается (лимит запросов, сбой связи);
  sleep   — — —, всплывают «z» (микрофон выключен);
  sad     — глаза ниже, брови домиком (не получилось, нет связи);
  hungry  — тащат файл: смотрит вниз, рот «О» — сейчас проглотит.

Смена эмоции с другой формой глаз (точки → дуги → спирали) идёт через
моргание: глаза закрываются в одной форме и открываются в другой — без
«перетекания» фигур, как у живого.

Логика (Face.step) отдельно от рисования (Face.paint): что сейчас с глазами —
проверяется тестами без экрана.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient


@dataclass(frozen=True)
class Emo:
    family: str          # dot | arc | line | spiral
    w: float             # ширина глаза (доля высоты тельца)
    h: float             # высота глаза
    dy: float = 0.0      # сдвиг глаз вниз
    look: tuple[float, float] | None = None    # взгляд закреплён (иначе — за курсором)


EMOTIONS: dict[str, Emo] = {
    "calm": Emo("dot", 0.13, 0.26),
    "listen": Emo("dot", 0.15, 0.31, -0.02),
    "think": Emo("dot", 0.12, 0.20, -0.01, (0.65, -0.85)),
    "talk": Emo("dot", 0.13, 0.22, -0.03),
    "happy": Emo("arc", 0.17, 0.11, -0.02, (0.0, 0.0)),
    "alert": Emo("dot", 0.17, 0.18, -0.02, (0.0, 0.0)),
    "dizzy": Emo("spiral", 0.19, 0.19, -0.01, (0.0, 0.0)),
    "sleep": Emo("line", 0.16, 0.035, 0.03, (0.0, 0.0)),
    "sad": Emo("dot", 0.12, 0.17, 0.05, (0.0, 0.6)),
    "hungry": Emo("dot", 0.15, 0.27, -0.06, (0.0, 0.9)),
}
BLINK_SEC = 0.16          # обычное моргание
SWITCH_SEC = 0.24         # моргание со сменой формы глаз — чуть медленнее
EYE_RGB = (20, 24, 31)
AMBER = (255, 176, 46)


class Face:
    def __init__(self, seed: int = 7):
        self._rng = random.Random(seed)
        self.emotion = "calm"
        self.family = "dot"
        self.since = 0.0                      # когда сменилась эмоция (для прыжка «рад»)
        self.clock = 0.0
        self.level = 0.0
        e = EMOTIONS["calm"]
        self.eye_w, self.eye_h, self.eye_dy = e.w, e.h, e.dy
        self.look = [0.0, 0.0]
        self._look_target = (0.0, 0.0)
        self.open = 1.0                       # 1 — глаза открыты, 0 — закрыты
        self._blink_t = -1.0                  # <0 — не моргает
        self._blink_len = BLINK_SEC
        self._switch_to: str | None = None
        self._next_blink = 2.0 + self._rng.uniform(0.0, 2.0)

    # ── вход ────────────────────────────────────────────────────────────────
    def set_emotion(self, name: str):
        if name not in EMOTIONS or name == self.emotion:
            return
        self.emotion, self.since = name, self.clock
        fam = EMOTIONS[name].family
        if fam != self.family or self._switch_to:
            self._switch_to = fam
            self._blink_t, self._blink_len = 0.0, SWITCH_SEC

    def look_at(self, x: float, y: float):
        """Куда смотреть: -1..1 по каждой оси (курсор слева/справа, выше/ниже)."""
        self._look_target = (max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y)))

    # ── время ───────────────────────────────────────────────────────────────
    def step(self, dt: float, level: float = 0.0):
        self.clock += dt
        # Громкость: вверх быстро, вниз медленно — рот не дребезжит.
        k = 18.0 if level > self.level else 6.0
        self.level += (level - self.level) * (1 - math.exp(-dt * k))
        # Моргание: само — только в форме «точки», и не у спящего/испуганного.
        if (self._blink_t < 0 and self.family == "dot" and self.emotion not in ("alert",)
                and self.clock >= self._next_blink):
            self._blink_t, self._blink_len = 0.0, BLINK_SEC
        if self._blink_t >= 0:
            self._blink_t += dt
            u = self._blink_t / self._blink_len
            self.open = min(1.0, abs(1.0 - 2.0 * min(1.0, u)))
            if u >= 0.5 and self._switch_to:      # закрылись — открываемся уже другими
                self.family, self._switch_to = self._switch_to, None
            if u >= 1.0:
                self._blink_t, self.open = -1.0, 1.0
                gap = self._rng.uniform(2.2, 5.0)
                if self._rng.random() < 0.15:     # иногда — двойное моргание
                    gap = 0.18
                self._next_blink = self.clock + gap
        e = EMOTIONS[self.emotion]
        a = 1 - math.exp(-dt * 14)
        self.eye_w += (e.w - self.eye_w) * a
        self.eye_h += (e.h - self.eye_h) * a
        self.eye_dy += (e.dy - self.eye_dy) * a
        tx, ty = e.look if e.look is not None else self._look_target
        if self.emotion == "think":               # думает — взгляд медленно бродит
            tx += 0.25 * math.sin(self.clock * 1.3)
        b = 1 - math.exp(-dt * 9)
        self.look[0] += (tx - self.look[0]) * b
        self.look[1] += (ty - self.look[1]) * b

    # ── рисование ───────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, cy: float, size: float, rgb=(48, 208, 190), glow: float = 1.0):
        """size — высота тельца. Рисует вокруг (cx, cy)."""
        t_emo = self.clock - self.since
        p.save()
        # Движение тельца: «рад» — подпрыгивает, «слушает» — покачивается,
        # «головокружение» — качается из стороны в сторону.
        dy, rot, squash = 0.0, 0.0, 0.0
        if self.emotion == "happy":
            hop = abs(math.sin(t_emo * 9.0)) * math.exp(-t_emo * 2.2)
            dy = -size * 0.16 * hop
            squash = 0.08 * (1 - hop) * math.exp(-t_emo * 2.2)
        elif self.emotion == "listen":
            dy = math.sin(self.clock * 3.2) * size * 0.035
        elif self.emotion == "dizzy":
            rot = 7.0 * math.sin(self.clock * 5.0)
        elif self.emotion == "hungry":
            squash = -0.05 * (0.5 + 0.5 * math.sin(self.clock * 8))
        bw, bh = size * 1.16 * (1 + squash), size * (1 - squash)
        p.translate(cx, cy + dy)
        if rot:
            p.rotate(rot)
        r, g, b = rgb
        if glow > 0:
            rg = QRadialGradient(QPointF(0, 0), size * 1.05)
            rg.setColorAt(0.0, QColor(r, g, b, int(95 * glow)))
            rg.setColorAt(0.55, QColor(r, g, b, int(38 * glow)))
            rg.setColorAt(1.0, QColor(r, g, b, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QPointF(0, 0), size * 1.05, size * 1.05)
        body = QRectF(-bw / 2, -bh / 2, bw, bh)
        path = QPainterPath()
        path.addRoundedRect(body, size * 0.40, size * 0.40)
        lg = QLinearGradient(body.topLeft(), body.bottomLeft())
        lg.setColorAt(0.0, _mix((252, 253, 255), rgb, 0.04))
        lg.setColorAt(1.0, _mix((206, 213, 222), rgb, 0.12))
        p.fillPath(path, lg)
        # Блик сверху — объём, как у гладкого камешка.
        p.setPen(QPen(QColor(255, 255, 255, 170), max(0.8, size * 0.03)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        hl = body.adjusted(size * 0.16, size * 0.07, -size * 0.16, 0)
        p.drawArc(hl, 30 * 16, 120 * 16)
        self._eyes(p, size)
        self._extras(p, size, body)
        p.restore()

    def _eyes(self, p: QPainter, s: float):
        ink = QColor(*EYE_RGB)
        gap = s * 0.21
        lx, ly = self.look[0] * s * 0.09, self.look[1] * s * 0.07
        ey = self.eye_dy * s + ly
        w, h = self.eye_w * s, max(self.eye_h * s * self.open, s * 0.025)
        for side in (-1, 1):
            ex = side * gap + lx
            if self.family == "dot":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ink)
                p.drawRoundedRect(QRectF(ex - w / 2, ey - h / 2, w, h), min(w, h) / 2, min(w, h) / 2)
                if h > s * 0.12:                      # блик в глазу — живой взгляд
                    p.setBrush(QColor(255, 255, 255, 200))
                    rr = w * 0.22
                    p.drawEllipse(QPointF(ex + w * 0.16, ey - h * 0.22), rr, rr)
            elif self.family == "arc":                # ^ ^
                p.setPen(QPen(ink, max(1.2, s * 0.075), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                hh = max(h, s * 0.03) * 2
                p.drawArc(QRectF(ex - w / 2, ey - hh / 2, w, hh * 1.4), 20 * 16, 140 * 16)
            elif self.family == "line":
                p.setPen(QPen(ink, max(1.2, s * 0.06), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(ex - w / 2, ey), QPointF(ex + w / 2, ey))
            elif self.family == "spiral":
                p.setPen(QPen(ink, max(0.9, s * 0.032), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(_spiral(ex, ey, w * 0.62 * max(0.2, self.open), self.clock * 7 * side))
        if self.emotion == "sad" and self.family == "dot":     # брови домиком
            p.setPen(QPen(ink, max(1.0, s * 0.05), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            by = ey - self.eye_h * s / 2 - s * 0.07
            for side in (-1, 1):
                ex = side * gap + lx
                # внутренний край выше внешнего — грусть, а не злость
                p.drawLine(QPointF(ex - side * w * 0.9, by - s * 0.04), QPointF(ex + side * w * 0.5, by + s * 0.03))

    def _extras(self, p: QPainter, s: float, body: QRectF):
        ink = QColor(*EYE_RGB)
        mouth_y = self.eye_dy * s + s * 0.23 + self.look[1] * s * 0.04
        if self.emotion == "talk" and self.level > 0.04:
            mh = s * (0.03 + 0.13 * min(1.0, self.level * 1.4))
            mw = s * 0.15
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(-mw / 2, mouth_y - mh / 2, mw, mh), mh / 2, mh / 2)
        elif self.emotion == "hungry":
            k = 0.8 + 0.2 * math.sin(self.clock * 8)
            mw, mh = s * 0.2 * k, s * 0.17 * k
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
            p.drawEllipse(QPointF(0, mouth_y + s * 0.02), mw / 2, mh / 2)
        elif self.emotion == "alert":
            # Янтарный значок «!» на плече — как уведомление на иконке.
            c = QPointF(body.left() + s * 0.06, body.top() + s * 0.06)
            rr = s * 0.17 * (1.0 + 0.08 * math.sin(self.clock * 7))
            p.setPen(QPen(QColor(18, 18, 22), max(1.0, s * 0.04)))
            p.setBrush(QColor(*AMBER))
            p.drawEllipse(c, rr, rr)
            _glyph(p, c, "!", rr * 1.5, QColor(30, 20, 0))
        elif self.emotion == "think":
            # «…» — три точки по очереди, справа сверху.
            for i in range(3):
                a = 0.35 + 0.65 * max(0.0, math.sin(self.clock * 5 - i * 0.9))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, int(235 * a)))
                p.drawEllipse(QPointF(body.right() + s * (0.02 + i * 0.12), body.top() - s * (0.0 + i * 0.1)),
                              s * 0.05, s * 0.05)
        elif self.emotion == "sleep":
            for i in range(2):
                u = (self.clock * 0.45 + i * 0.5) % 1.0
                a = int(230 * math.sin(math.pi * u))
                c = QPointF(body.right() + s * (0.05 + 0.25 * u), body.top() + s * (0.1 - 0.45 * u))
                _glyph(p, c, "z", s * (0.26 + 0.12 * u), QColor(255, 255, 255, a))


def _mix(a, b, k: float) -> QColor:
    return QColor(*(int(x + (y - x) * k) for x, y in zip(a, b)))


def _spiral(cx: float, cy: float, r: float, phase: float) -> QPainterPath:
    path = QPainterPath()
    turns, n = 1.8, 48
    for i in range(n + 1):
        u = i / n
        ang = phase + u * turns * 2 * math.pi
        rad = r * u
        pt = QPointF(cx + math.cos(ang) * rad, cy + math.sin(ang) * rad)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    return path


def _glyph(p: QPainter, c: QPointF, ch: str, size: float, color: QColor):
    f = QFont("Segoe UI", 1)
    f.setPixelSize(max(1, int(size)))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QPen(color))
    p.drawText(QRectF(c.x() - size, c.y() - size, size * 2, size * 2), int(Qt.AlignmentFlag.AlignCenter), ch)
