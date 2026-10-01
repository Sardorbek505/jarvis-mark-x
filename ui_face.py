"""Лицо Джарвиса в капсуле — «ядро HUD» с глазами-визорами.

Тёмная стеклянная сфера, по краю — кольцо из сегментов, как кольца HUD
Джарвиса: вращается, ускоряется, когда он думает, пульсирует от голоса.
Внутри — два светящихся глаза-визора цвета состояния (не чёрные точки
маскота, а свет изнутри, как у интерфейса Старка). Эмоции:

  calm    — ждёт: кольцо медленно плывёт, визоры моргают, взгляд за курсором;
  listen  — визоры шире, кольцо ускоряется;
  think   — визоры сужены и смотрят вверх-вбок, кольцо крутится, рядом «…»;
  talk    — под визорами «голос»: полоска пульсирует от громкости;
  happy   — визоры дугами ^ ^, ядро подпрыгивает (задача выполнена);
  alert   — кольцо янтарное и мигает, визоры распахнуты, «!» (нужно разрешение);
  dizzy   — визоры-спирали, кольцо сбилось (лимит запросов, сбой);
  sleep   — кольцо погасло, визоры — тонкие линии, всплывают «z»;
  sad     — визоры опущены к краям и тусклее (не получилось, нет связи);
  hungry  — тащат файл: кольцо раскрывается снизу, как рот, — сейчас проглотит.

Смена эмоции с другой формой глаз (визоры → дуги → спирали) идёт через
моргание: закрылись в одной форме, открылись в другой.

Логика (Face.step) отдельно от рисования (Face.paint): что сейчас с глазами —
проверяется тестами без экрана.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient


@dataclass(frozen=True)
class Emo:
    family: str          # dot (визор) | arc | line | spiral
    w: float             # ширина глаза (доля диаметра ядра)
    h: float             # высота глаза
    dy: float = 0.0      # сдвиг глаз вниз
    look: tuple[float, float] | None = None    # взгляд закреплён (иначе — за курсором)
    tilt: float = 0.0    # наклон визоров, °: >0 — внешние края вниз (грусть)
    spin: float = 30.0   # скорость кольца, °/с


EMOTIONS: dict[str, Emo] = {
    "calm": Emo("dot", 0.20, 0.08, -0.02),
    "listen": Emo("dot", 0.21, 0.12, -0.02, spin=70.0),
    "think": Emo("dot", 0.18, 0.055, -0.03, (0.6, -0.8), spin=240.0),
    "talk": Emo("dot", 0.20, 0.085, -0.05, spin=45.0),
    "happy": Emo("arc", 0.21, 0.08, -0.02, (0.0, 0.0), spin=120.0),
    "alert": Emo("dot", 0.16, 0.15, -0.02, (0.0, 0.0), spin=0.0),
    "dizzy": Emo("spiral", 0.20, 0.20, -0.01, (0.0, 0.0), spin=420.0),
    "sleep": Emo("line", 0.19, 0.03, 0.03, (0.0, 0.0), spin=0.0),
    "sad": Emo("dot", 0.18, 0.06, 0.04, (0.0, 0.6), tilt=16.0, spin=10.0),
    "hungry": Emo("dot", 0.19, 0.13, -0.06, (0.0, 0.9), spin=60.0),
}
BLINK_SEC = 0.16          # обычное моргание
SWITCH_SEC = 0.24         # моргание со сменой формы глаз — чуть медленнее
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
        self.tilt = 0.0
        self.ring = 0.0                       # поворот кольца, °
        self._spin = e.spin
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
        self.tilt += (e.tilt - self.tilt) * a
        # Кольцо: скорость меняется плавно, угол накапливается — без рывков.
        spin = e.spin + (self.level * 260 if self.emotion == "talk" else 0.0)
        self._spin += (spin - self._spin) * (1 - math.exp(-dt * 4))
        self.ring = (self.ring + self._spin * dt) % 360
        tx, ty = e.look if e.look is not None else self._look_target
        if self.emotion == "think":               # думает — взгляд медленно бродит
            tx += 0.25 * math.sin(self.clock * 1.3)
        b = 1 - math.exp(-dt * 9)
        self.look[0] += (tx - self.look[0]) * b
        self.look[1] += (ty - self.look[1]) * b

    # ── рисование ───────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, cy: float, size: float, rgb=(48, 208, 190), glow: float = 1.0):
        """size — диаметр ядра. Рисует вокруг (cx, cy)."""
        t_emo = self.clock - self.since
        p.save()
        dy, rot, sq = 0.0, 0.0, 0.0
        if self.emotion == "happy":                   # подпрыгивает и чуть сплющивается, приземляясь
            hop = abs(math.sin(t_emo * 9.0)) * math.exp(-t_emo * 2.2)
            dy = -size * 0.16 * hop
            sq = 0.07 * (1 - hop) * math.exp(-t_emo * 2.2)
        elif self.emotion == "listen":
            dy = math.sin(self.clock * 3.2) * size * 0.03
        elif self.emotion == "dizzy":
            rot = 8.0 * math.sin(self.clock * 5.0)
        p.translate(cx, cy + dy)
        if rot:
            p.rotate(rot)
        if sq:
            p.scale(1 + sq, 1 - sq)
        rad = size / 2
        light = _mix(rgb, (255, 255, 255), 0.55)      # свет визоров и кольца
        dimk = 0.35 if self.emotion == "sleep" else (0.7 if self.emotion == "sad" else 1.0)
        r, g, b = rgb
        if glow > 0:
            rg = QRadialGradient(QPointF(0, 0), size * 0.95)
            rg.setColorAt(0.0, QColor(r, g, b, int(90 * glow * dimk)))
            rg.setColorAt(0.55, QColor(r, g, b, int(34 * glow * dimk)))
            rg.setColorAt(1.0, QColor(r, g, b, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QPointF(0, 0), size * 0.95, size * 0.95)
        # Стеклянное ядро: тёмное, с подсветкой цветом состояния изнутри.
        core = QRadialGradient(QPointF(0, -rad * 0.35), rad * 1.25)
        core.setColorAt(0.0, _mix((10, 13, 18), rgb, 0.30 * dimk))
        core.setColorAt(0.7, QColor(9, 11, 16))
        core.setColorAt(1.0, QColor(4, 5, 8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(core)
        p.drawEllipse(QPointF(0, 0), rad, rad)
        p.setPen(QPen(QColor(255, 255, 255, 50), max(0.7, size * 0.025)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        hl = rad * 0.72
        p.drawArc(QRectF(-hl, -hl, 2 * hl, 2 * hl), 100 * 16, 60 * 16)     # блик стекла
        self._ring(p, size, light, dimk)
        self._eyes(p, size, light, dimk)
        self._extras(p, size, light)
        p.restore()

    def _ring(self, p: QPainter, s: float, light: QColor, dimk: float):
        """Кольцо HUD по краю ядра: три сегмента; «голоден» — раскрыто снизу, как рот."""
        rad = s / 2
        pw = max(1.0, s * 0.07)
        rr = rad - pw / 2
        box = QRectF(-rr, -rr, 2 * rr, 2 * rr)
        col = QColor(light)
        a = 225 * dimk
        if self.emotion == "alert":
            col = QColor(*AMBER)
            a = 120 + 135 * (0.5 + 0.5 * math.sin(self.clock * 9))
        if self.emotion == "talk":
            pw *= 1.0 + 0.6 * min(1.0, self.level * 1.5)
        col.setAlpha(int(a))
        p.setPen(QPen(col, pw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.emotion == "hungry":
            gap = 80 + 30 * math.sin(self.clock * 8)           # «рот» внизу дышит
            p.drawArc(box, int((-90 + gap / 2) * 16), int((360 - gap) * 16))
            return
        for i in range(3):
            start = -self.ring + i * 120
            p.drawArc(box, int(start * 16), int(84 * 16))
        if s >= 24:                                             # тонкое внутреннее кольцо — деталь HUD
            ir = rad * 0.8
            p.setPen(QPen(QColor(light.red(), light.green(), light.blue(), int(45 * dimk)), max(0.6, s * 0.018)))
            p.drawEllipse(QPointF(0, 0), ir, ir)

    def _eyes(self, p: QPainter, s: float, light: QColor, dimk: float):
        gap = s * 0.16
        lx, ly = self.look[0] * s * 0.07, self.look[1] * s * 0.06
        ey = self.eye_dy * s + ly
        w, h = self.eye_w * s, max(self.eye_h * s * self.open, s * 0.02)
        core = QColor(light)
        core.setAlpha(int(255 * max(0.45, dimk)))
        halo = QColor(light)
        halo.setAlpha(int(70 * dimk))
        for side in (-1, 1):
            ex = side * gap + lx
            p.save()
            p.translate(ex, ey)
            if self.family == "dot":                            # визор: светящаяся щель
                p.rotate(side * self.tilt)
                p.setPen(Qt.PenStyle.NoPen)
                hw, hh = w / 2 + s * 0.035, h / 2 + s * 0.035
                p.setBrush(halo)
                p.drawRoundedRect(QRectF(-hw, -hh, 2 * hw, 2 * hh), hh, hh)
                p.setBrush(core)
                p.drawRoundedRect(QRectF(-w / 2, -h / 2, w, h), min(w, h) / 2, min(w, h) / 2)
            elif self.family == "arc":                          # ^ ^
                hh = max(h, s * 0.03) * 2
                arc = QRectF(-w / 2, -hh / 2, w, hh * 1.4)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(halo, max(2.0, s * 0.13), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(arc, 20 * 16, 140 * 16)
                p.setPen(QPen(core, max(1.1, s * 0.06), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(arc, 20 * 16, 140 * 16)
            elif self.family == "line":
                p.setPen(QPen(core, max(1.0, s * 0.045), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(-w / 2, 0), QPointF(w / 2, 0))
            elif self.family == "spiral":
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(core, max(0.9, s * 0.035), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawPath(_spiral(0, 0, w * 0.55 * max(0.2, self.open), self.clock * 7 * side))
            p.restore()

    def _extras(self, p: QPainter, s: float, light: QColor):
        if self.emotion == "talk":
            # «Голос»: светящаяся полоска под визорами пульсирует от громкости.
            k = min(1.0, self.level * 1.5)
            vw = s * (0.08 + 0.26 * k)
            vy = self.eye_dy * s + s * 0.17
            col = QColor(light)
            col.setAlpha(int(110 + 145 * k))
            p.setPen(QPen(col, max(1.0, s * 0.045), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(-vw / 2, vy), QPointF(vw / 2, vy))
        elif self.emotion == "alert":
            c = QPointF(s * 0.38, -s * 0.38)
            rr = s * 0.15 * (1.0 + 0.08 * math.sin(self.clock * 7))
            p.setPen(QPen(QColor(10, 10, 14), max(1.0, s * 0.035)))
            p.setBrush(QColor(*AMBER))
            p.drawEllipse(c, rr, rr)
            _glyph(p, c, "!", rr * 1.5, QColor(30, 20, 0))
        elif self.emotion == "think":
            for i in range(3):
                a = 0.35 + 0.65 * max(0.0, math.sin(self.clock * 5 - i * 0.9))
                col = QColor(light)
                col.setAlpha(int(235 * a))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawEllipse(QPointF(s * (0.5 + i * 0.12), -s * (0.42 + i * 0.1)), s * 0.045, s * 0.045)
        elif self.emotion == "sleep":
            for i in range(2):
                u = (self.clock * 0.45 + i * 0.5) % 1.0
                a = int(230 * math.sin(math.pi * u))
                c = QPointF(s * (0.5 + 0.25 * u), -s * (0.4 + 0.45 * u))
                _glyph(p, c, "z", s * (0.26 + 0.12 * u), QColor(255, 255, 255, a))


def _mix(a, b, k: float) -> QColor:
    if isinstance(a, QColor):
        a = (a.red(), a.green(), a.blue())
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
