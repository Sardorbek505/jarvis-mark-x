"""Интро Джарвиса на весь экран (два хлопка). Сценарий, реплики и звук — core/intro.py.

Как в образце владельца: тёмно-синий экран, шар в золотом кольце, от него —
сеть больших узлов с иконками и подписями, тонкие дуги по всему экрану,
внизу — субтитры того, что говорит Джарвис (слово за словом). Лица нет.

Кадр — функция от времени (paint_at): окно и превью-видео рисуют одно и то
же, тест проверяет любой момент без таймеров. Клик или Esc — пропустить.
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QConicalGradient, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPolygonF, QRadialGradient)
from PyQt6.QtWidgets import QApplication, QWidget

from core import intro as S
from orb import DotOrb

CYAN = QColor("#5fd4ff")
ICE = QColor("#cdf3ff")
BLUE = QColor("#2a7fff")
GOLD = QColor("#ffc04d")
WHITE = QColor("#f4fbff")
GREY = QColor("#7d93ad")
GREEN = QColor("#46e8a0")
RED = QColor("#ff4f6a")
NAVY_C = QColor("#0c2f5f")
NAVY_E = QColor("#020812")

UI_FONT = "Bahnschrift"            # в Windows есть всегда; узкий «технический» шрифт
MONO_FONT = "Consolas"


def _c(base: QColor, a: float) -> QColor:
    c = QColor(base)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _out(x: float, k: float = 3.0) -> float:          # вход в кадр — тормозит к концу
    x = _clamp(x)
    return 1 - (1 - x) ** k


def _in(x: float, k: float = 3.0) -> float:           # уход — разгоняется
    return _clamp(x) ** k


def _back(x: float, s: float = 1.9) -> float:         # с перелётом (follow-through)
    x = _clamp(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def _font(family: str, px: float, weight=QFont.Weight.Normal, spacing: float = 100) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(8, int(px)))
    f.setWeight(weight)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
    return f


# ── иконки: тонкие линии в квадрате 1×1, рисуются «пером» (draw-on) ───────────

def _icon_path(name: str) -> QPainterPath:
    p = QPainterPath()
    if name == "music":
        p.moveTo(0.36, 0.72); p.lineTo(0.36, 0.22); p.lineTo(0.78, 0.14); p.lineTo(0.78, 0.64)  # noqa: E702
        p.moveTo(0.36, 0.34); p.lineTo(0.78, 0.26)                                              # noqa: E702
        p.addEllipse(QPointF(0.27, 0.74), 0.10, 0.08)
        p.addEllipse(QPointF(0.69, 0.66), 0.10, 0.08)
    elif name == "phone":
        p.moveTo(0.28, 0.16)
        p.cubicTo(0.18, 0.22, 0.14, 0.34, 0.22, 0.48)
        p.cubicTo(0.34, 0.68, 0.48, 0.80, 0.64, 0.86)
        p.cubicTo(0.76, 0.90, 0.84, 0.82, 0.86, 0.74)
        p.lineTo(0.70, 0.62); p.lineTo(0.60, 0.70)                                               # noqa: E702
        p.cubicTo(0.48, 0.64, 0.38, 0.52, 0.32, 0.42)
        p.lineTo(0.40, 0.32); p.closeSubpath()                                                   # noqa: E702
        p.moveTo(0.58, 0.18); p.cubicTo(0.70, 0.20, 0.80, 0.30, 0.82, 0.42)                      # noqa: E702
        p.moveTo(0.58, 0.30); p.cubicTo(0.64, 0.32, 0.69, 0.37, 0.70, 0.43)                      # noqa: E702
    elif name == "memory":
        p.addRoundedRect(QRectF(0.26, 0.26, 0.48, 0.48), 0.06, 0.06)
        p.addRoundedRect(QRectF(0.38, 0.38, 0.24, 0.24), 0.03, 0.03)
        for k in (0.36, 0.5, 0.64):
            for a, b in (((k, 0.26), (k, 0.14)), ((k, 0.74), (k, 0.86)), ((0.26, k), (0.14, k)), ((0.74, k), (0.86, k))):
                p.moveTo(*a); p.lineTo(*b)                                                       # noqa: E702
    elif name == "calendar":
        p.addRoundedRect(QRectF(0.18, 0.24, 0.64, 0.58), 0.07, 0.07)
        p.moveTo(0.18, 0.40); p.lineTo(0.82, 0.40)                                               # noqa: E702
        p.moveTo(0.34, 0.14); p.lineTo(0.34, 0.30); p.moveTo(0.66, 0.14); p.lineTo(0.66, 0.30)   # noqa: E702
        for x in (0.32, 0.5, 0.68):
            for y in (0.54, 0.68):
                p.addEllipse(QPointF(x, y), 0.025, 0.025)
    elif name == "weather":
        p.addEllipse(QPointF(0.40, 0.38), 0.13, 0.13)
        for k in range(8):
            a = k * math.pi / 4
            p.moveTo(0.40 + math.cos(a) * 0.19, 0.38 + math.sin(a) * 0.19)
            p.lineTo(0.40 + math.cos(a) * 0.26, 0.38 + math.sin(a) * 0.26)
        p.moveTo(0.36, 0.80)
        p.cubicTo(0.24, 0.80, 0.24, 0.62, 0.38, 0.62)
        p.cubicTo(0.42, 0.48, 0.64, 0.48, 0.68, 0.60)
        p.cubicTo(0.84, 0.60, 0.86, 0.80, 0.72, 0.80)
        p.closeSubpath()
    elif name == "globe":
        p.addEllipse(QPointF(0.5, 0.5), 0.34, 0.34)
        p.addEllipse(QPointF(0.5, 0.5), 0.15, 0.34)
        p.moveTo(0.16, 0.5); p.lineTo(0.84, 0.5)                                                 # noqa: E702
        p.moveTo(0.22, 0.32); p.cubicTo(0.40, 0.38, 0.60, 0.38, 0.78, 0.32)                      # noqa: E702
        p.moveTo(0.22, 0.68); p.cubicTo(0.40, 0.62, 0.60, 0.62, 0.78, 0.68)                      # noqa: E702
    elif name == "telegram":
        p.moveTo(0.14, 0.48); p.lineTo(0.84, 0.20); p.lineTo(0.72, 0.80)                         # noqa: E702
        p.lineTo(0.48, 0.64); p.lineTo(0.14, 0.48)                                               # noqa: E702
        p.moveTo(0.84, 0.20); p.lineTo(0.40, 0.58); p.lineTo(0.42, 0.78); p.lineTo(0.48, 0.64)   # noqa: E702
    elif name == "vision":
        p.moveTo(0.12, 0.5)
        p.cubicTo(0.28, 0.24, 0.72, 0.24, 0.88, 0.5)
        p.cubicTo(0.72, 0.76, 0.28, 0.76, 0.12, 0.5)
        p.addEllipse(QPointF(0.5, 0.5), 0.12, 0.12)
        for a, b in (((0.5, 0.06), (0.5, 0.18)), ((0.5, 0.82), (0.5, 0.94))):
            p.moveTo(*a); p.lineTo(*b)                                                           # noqa: E702
    elif name == "video":
        p.addRoundedRect(QRectF(0.14, 0.24, 0.72, 0.52), 0.12, 0.12)
        p.moveTo(0.43, 0.38); p.lineTo(0.62, 0.5); p.lineTo(0.43, 0.62); p.closeSubpath()        # noqa: E702
    elif name == "study":
        p.moveTo(0.10, 0.40); p.lineTo(0.5, 0.22); p.lineTo(0.90, 0.40); p.lineTo(0.5, 0.58)     # noqa: E702
        p.closeSubpath()
        p.moveTo(0.26, 0.48); p.lineTo(0.26, 0.66)                                               # noqa: E702
        p.cubicTo(0.40, 0.78, 0.60, 0.78, 0.74, 0.66); p.lineTo(0.74, 0.48)                      # noqa: E702
        p.moveTo(0.90, 0.40); p.lineTo(0.90, 0.62)                                               # noqa: E702
    return p


def _draw_icon(p: QPainter, name: str, c: QPointF, size: float, color: QColor, reveal: float, width: float):
    """Иконка «рисуется пером»: штрих проходит reveal (0…1) своей длины."""
    if reveal <= 0:
        return
    path = _icon_path(name)
    p.save()
    p.translate(c.x() - size / 2, c.y() - size / 2)
    p.scale(size, size)
    pen = QPen(color, width / size)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if reveal < 1:
        total = path.length() / pen.widthF()
        pen.setDashPattern([max(0.01, total * reveal), total + 1])
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.restore()


# ── сцена ────────────────────────────────────────────────────────────────────

class IntroScene:
    """Всё состояние между кадрами — шар (у него своя физика) и голос."""

    def __init__(self, checks: dict | None = None, now: datetime | None = None):
        self.checks = checks or {}
        self.now = now
        self.orb = DotOrb(n=1100, seed=11)
        self.orb.set_shape("sphere")
        self._last_t = 0.0
        self.fade, self.end = S.T_FADE, S.T_END
        self.lines = [(at, None, text) for at, text in S.lines(self.checks)]
        self.env: list[float] = []                 # громкость голоса по 30 мс от начала интро
        self.env_step = 0.03
        rnd = random.Random(5)
        self._arcs = [(rnd.uniform(0, 2 * math.pi), rnd.uniform(0.9, 1.7), rnd.uniform(-0.9, 0.9),
                       rnd.uniform(0, 0.6), rnd.random()) for _ in range(46)]
        self._dust = [(rnd.random(), rnd.random(), rnd.uniform(0.4, 1.6), rnd.uniform(0.2, 0.7)) for _ in range(90)]
        self.stats = self._stats()

    # данные, которые видно на HUD: часы и загрузка — настоящие
    @staticmethod
    def _stats() -> tuple[int, int]:
        try:
            import psutil
            return int(psutil.cpu_percent(None)), int(psutil.virtual_memory().percent)
        except Exception:
            return 0, 0

    def set_voice(self, lines: list[tuple[float, float, str]], env: list[float], step: float = 0.03):
        """Реплики (начало, длительность, текст) и громкость голоса — от main после синтеза."""
        self.lines = list(lines)
        self.env, self.env_step = list(env), step

    def extend_to(self, end: float):
        """Джарвис ещё говорит — картинка держится и тает к end."""
        if end > self.end:
            self.end, self.fade = end, end - (S.T_END - S.T_FADE)

    def voice_level(self, t: float) -> float:
        i = int(t / self.env_step)
        return self.env[i] if 0 <= i < len(self.env) else 0.0

    # ── кадр ──────────────────────────────────────────────────────────────────
    def paint_at(self, p: QPainter, w: int, h: int, t: float):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        dt, self._last_t = max(0.0, t - self._last_t), t
        exit_k = _clamp((t - self.fade) / (self.end - self.fade))
        if exit_k >= 1:
            return
        self._collapse(p, w, h, t)
        if t < S.T_BOOT - 0.2:
            return
        p.save()
        p.setOpacity(1 - _in(_clamp((exit_k - 0.55) / 0.45), 2))     # последним гаснет фон
        self._background(p, w, h, t)
        p.restore()

        shake = 0.0
        if S.T_CHARGE + 1.0 < t < S.T_HIT:
            shake = 2.2 * _in((t - S.T_CHARGE - 1.0) / (S.T_HIT - S.T_CHARGE - 1.0))
        elif t >= S.T_HIT:
            shake = 9 * math.exp(-(t - S.T_HIT) / 0.12)
        dx = shake * math.sin(t * 97)
        dy = shake * math.cos(t * 83)
        cx, cy = w / 2 + dx, h * 0.455 + dy
        r = min(w, h) * 0.13
        # уход: шар сжимается и улетает наверх, к капсуле
        fly = _in(_clamp((exit_k - 0.25) / 0.6), 2.2)
        ox, oy = cx + (w / 2 - cx) * fly, cy + (h * 0.02 - cy) * fly
        rr = r * (1 - 0.92 * fly)

        hud_a = 1 - _in(_clamp(exit_k / 0.4))
        p.save()
        p.setOpacity(hud_a)
        self._hud(p, w, h, t)
        self._arcs_net(p, w, h, cx, cy, r, t)
        self._nodes(p, cx, cy, r, t, exit_k)
        self._scan(p, cx, cy, r, t)
        p.restore()
        self._core(p, ox, oy, rr, t, dt)
        self._shockwave(p, w, h, cx, cy, r, t)
        p.save()
        p.setOpacity(hud_a)
        self._subtitles(p, w, h, t)
        p.restore()
        self._flash(p, w, h, t)

    # 0,0–0,9: экран схлопывается в линию, линия — в точку («выключили ЭЛТ»)
    def _collapse(self, p, w, h, t):
        dark = _out(t / 0.12, 2) if t < S.T_BOOT else 1.0
        p.fillRect(0, 0, w, h, _c(NAVY_E, dark))
        if t > 0.6:
            return
        k = _clamp(t / 0.32)
        bh = max(2.0, h * (1 - _in(k, 2.4)))
        bw = w * (1 - _in(_clamp((t - 0.30) / 0.2), 2))
        if bw < 2:
            g = QRadialGradient(QPointF(w / 2, h / 2), 26)
            a = 1 - _clamp((t - 0.5) / 0.1)
            g.setColorAt(0, _c(WHITE, a))
            g.setColorAt(1, _c(CYAN, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(w / 2, h / 2), 26, 26)
            return
        band = QLinearGradient(0, h / 2 - bh / 2, 0, h / 2 + bh / 2)
        a = 0.7 + 0.3 * _in(k)                                   # полоса яркая, как гаснущий кинескоп
        band.setColorAt(0, _c(ICE, 0))
        band.setColorAt(0.5, _c(WHITE, a))
        band.setColorAt(1, _c(ICE, 0))
        p.fillRect(QRectF((w - bw) / 2, h / 2 - bh / 2, bw, bh), band)

    def _background(self, p, w, h, t):
        on = _out((t - S.T_BOOT + 0.2) / 1.4, 2)
        boost = 0.35 * math.exp(-max(0, t - S.T_HIT) / 0.8) * (t >= S.T_HIT)
        if on >= 1 and boost < 0.004:
            # Фон больше не меняется — рисуем готовую картинку: градиенты на весь
            # экран — самое дорогое в кадре (на 1080p ~треть времени).
            key = (w, h)
            if getattr(self, "_bg_key", None) != key:
                img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
                img.fill(Qt.GlobalColor.transparent)
                q = QPainter(img)
                self._paint_bg(q, w, h, 1.0, 0.0)
                q.end()
                self._bg_img, self._bg_key = img, key
            p.drawImage(0, 0, self._bg_img)
        else:
            self._paint_bg(p, w, h, on, boost)
        # пылинки в воздухе — глубина
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, s, sp in self._dust:
            yy = (y - t * 0.01 * sp) % 1.0
            p.setBrush(_c(ICE, 0.10 * on * s))
            p.drawEllipse(QPointF(x * w, yy * h), s, s)

    def _paint_bg(self, p, w, h, on, boost):
        g = QRadialGradient(QPointF(w / 2, h * 0.455), max(w, h) * 0.75)
        g.setColorAt(0, _c(NAVY_C.lighter(int(100 + 60 * boost)), on))
        g.setColorAt(0.55, _c(QColor("#061a36"), on))
        g.setColorAt(1, _c(NAVY_E, 1))
        p.fillRect(0, 0, w, h, g)
        vig = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.72)
        vig.setColorAt(0.6, _c(NAVY_E, 0))
        vig.setColorAt(1, _c(NAVY_E, 0.75))
        p.fillRect(0, 0, w, h, vig)

    # HUD по краям: уголки, часы, загрузка, полоса голоса — по очереди
    def _hud(self, p, w, h, t):
        m = min(w, h) * 0.035
        L = min(w, h) * 0.06
        for k, (x, y, sx, sy) in enumerate(((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1))):
            g = _out((t - 1.0 - 0.09 * k) / 0.45)
            if g <= 0:
                continue
            p.setPen(QPen(_c(CYAN, 0.55), 1.4))
            p.drawLine(QPointF(x, y), QPointF(x + sx * L * g, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + sy * L * g))
        a = _out((t - 1.35) / 0.5)
        if a > 0:
            now = self.now or datetime.now()
            p.setPen(_c(WHITE, a))
            p.setFont(_font(UI_FONT, h * 0.05, QFont.Weight.Light))
            p.drawText(QPointF(m * 1.6, m * 1.6 + h * 0.045), now.strftime("%H:%M"))
            p.setPen(_c(GREY, a))
            p.setFont(_font(MONO_FONT, h * 0.014, spacing=110))
            p.drawText(QPointF(m * 1.7, m * 1.6 + h * 0.072), now.strftime("%d.%m.%Y  ·  JARVIS MARK X"))
        a = _out((t - 1.6) / 0.5)
        if a > 0:
            cpu, ram = self.stats
            p.setFont(_font(MONO_FONT, h * 0.018, spacing=110))
            for i, (name, val) in enumerate((("CPU", cpu), ("RAM", ram))):
                y = m * 1.6 + h * 0.03 + i * h * 0.03
                x1 = w - m * 1.6
                p.setPen(_c(GREY, a))
                p.drawText(QRectF(x1 - w * 0.16, y - h * 0.02, w * 0.05, h * 0.025), Qt.AlignmentFlag.AlignLeft, name)
                bar = QRectF(x1 - w * 0.10, y - h * 0.012, w * 0.075, 3)
                p.fillRect(bar, _c(CYAN, 0.15 * a))
                fill = bar.adjusted(0, 0, -bar.width() * (1 - val / 100 * _out((t - 1.6) / 1.2)), 0)
                p.fillRect(fill, _c(CYAN, 0.9 * a))
                p.setPen(_c(WHITE, a))
                p.drawText(QRectF(x1 - w * 0.02, y - h * 0.02, w * 0.02, h * 0.025), Qt.AlignmentFlag.AlignRight, f"{val}")
        # пилюля с волной голоса внизу по центру (как в образце)
        a = _out((t - 1.8) / 0.5)
        if a > 0:
            pw, ph = w * 0.11, h * 0.034
            box = QRectF(w / 2 - pw / 2, h * 0.935, pw, ph)
            p.setPen(QPen(_c(CYAN, 0.35 * a), 1))
            p.setBrush(_c(QColor("#04142a"), 0.8 * a))
            p.drawRoundedRect(box, ph / 2, ph / 2)
            lv = self.voice_level(t)
            n = 15
            for i in range(n):
                k = 1 - abs(i - n // 2) / (n / 2)
                amp = 0.18 + 0.82 * lv * k * (0.6 + 0.4 * math.sin(t * 18 + i * 1.7))
                bx = box.left() + pw * (0.2 + 0.6 * i / (n - 1))
                bh = ph * 0.62 * max(0.12, amp)
                p.setPen(QPen(_c(GOLD if i == n // 2 else CYAN, 0.9 * a), 2, cap=Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(bx, box.center().y() - bh / 2), QPointF(bx, box.center().y() + bh / 2))

    # шар в золотом кольце
    def _core(self, p, cx, cy, r, t, dt):
        if t < S.T_CHARGE or r < 1:
            return
        charge = _out((t - S.T_CHARGE) / (S.T_HIT - S.T_CHARGE), 2)
        pull = 1 - 0.08 * math.sin(math.pi * _clamp((t - (S.T_HIT - 0.25)) / 0.25)) * (t < S.T_HIT)   # «вдох»
        settle = 1 + 0.14 * math.exp(-max(0, t - S.T_HIT) / 0.18) * math.cos(14 * max(0, t - S.T_HIT)) * (t >= S.T_HIT)
        voice = self.voice_level(t)
        rr = r * pull * settle * (1 + 0.05 * voice)
        lit = 0.35 + 0.65 * charge if t < S.T_HIT else 1.0

        # ореол
        g = QRadialGradient(QPointF(cx, cy), rr * 3.2)
        g.setColorAt(0, _c(BLUE, 0.45 * lit + 0.25 * voice))
        g.setColorAt(0.35, _c(BLUE, 0.16 * lit))
        g.setColorAt(1, _c(BLUE, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), rr * 3.2, rr * 3.2)
        # тело шара
        body = QRadialGradient(QPointF(cx - rr * 0.25, cy - rr * 0.3), rr * 1.1)
        body.setColorAt(0, _c(QColor("#3d9bff"), lit))
        body.setColorAt(0.6, _c(QColor("#1450b8"), lit))
        body.setColorAt(1, _c(QColor("#08214f"), lit))
        p.setBrush(body)
        p.drawEllipse(QPointF(cx, cy), rr * 0.86, rr * 0.86)
        # точки и меридианы — объём
        if t >= S.T_CHARGE + 0.4:
            self.orb.step(min(dt, 0.05), level=0.2 + 0.7 * voice + 0.5 * math.exp(-max(0, t - S.T_HIT) / 0.4),
                          active=True)
            xs, ys, zs = self.orb.project(cx, cy, rr * 0.80)
            a = _out((t - S.T_CHARGE - 0.4) / 0.8)
            # точки пачками по глубине — drawPoints в разы быстрее тысячи кружков
            depth = (zs + 1) / 2
            for lo, hi in ((0.0, 0.35), (0.35, 0.6), (0.6, 0.8), (0.8, 1.01)):
                sel = (depth >= lo) & (depth < hi)
                if not sel.any():
                    continue
                mid = (lo + hi) / 2
                p.setPen(QPen(_c(ICE, a * (0.12 + 0.75 * mid ** 2)), 1.2 + 2.2 * mid, cap=Qt.PenCapStyle.RoundCap))
                p.drawPoints(QPolygonF([QPointF(x, y) for x, y in zip(xs[sel], ys[sel])]))
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(3):                                       # меридианы, медленно вращаются
            ph = t * 0.4 + k * math.pi / 3
            p.setPen(QPen(_c(ICE, 0.18 * lit), 1))
            p.drawEllipse(QPointF(cx, cy), rr * 0.86 * abs(math.cos(ph)), rr * 0.86)
        # золотое кольцо: дорисовывается при заряде, затем светится
        prog = _out((t - S.T_CHARGE) / (S.T_HIT - S.T_CHARGE - 0.1), 2)
        ring = QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr)
        span = int(-360 * 16 * prog)
        flare = math.exp(-max(0, t - S.T_HIT) / 0.5) * (t >= S.T_HIT) + 0.8 * math.exp(-max(0, t - S.T_FINAL) / 0.5) * (t >= S.T_FINAL)
        for width, alpha in ((rr * 0.34, 0.05), (rr * 0.18, 0.12), (rr * 0.09, 0.30), (rr * 0.045, 0.85), (rr * 0.018, 1.0)):
            col = _c(GOLD if width > rr * 0.02 else QColor("#fff2cf"), alpha * (0.6 + 0.4 * lit + 0.4 * flare + 0.3 * voice))
            p.setPen(QPen(col, width, cap=Qt.PenCapStyle.FlatCap))
            p.drawArc(ring, 90 * 16, span)
        # шкала-циферблат вокруг кольца
        if t > S.T_CHARGE + 0.2:
            a = _out((t - S.T_CHARGE - 0.2) / 0.8)
            p.save()
            p.translate(cx, cy)
            p.rotate(-t * 9)
            for i in range(120):
                if i / 120 > a:
                    break
                long = i % 10 == 0
                p.setPen(QPen(_c(CYAN, (0.55 if long else 0.25) * lit), 1.2))
                r0, r1 = rr * 1.16, rr * (1.26 if long else 1.21)
                ang = 2 * math.pi * i / 120
                p.drawLine(QPointF(math.cos(ang) * r0, math.sin(ang) * r0), QPointF(math.cos(ang) * r1, math.sin(ang) * r1))
            p.restore()
            p.setPen(QPen(_c(CYAN, 0.22 * a * lit), 1))
            p.drawEllipse(QPointF(cx, cy), rr * 1.38, rr * 1.38)

    def _shockwave(self, p, w, h, cx, cy, r, t):
        for at, power in ((S.T_HIT, 1.0), (S.T_FINAL, 0.55)):
            if not (at <= t < at + 1.0):
                continue
            k = _out((t - at) / 0.9, 2.5)
            rad = r * 1.1 + k * max(w, h) * 0.75 * power
            a = (1 - k) * 0.8 * power
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_c(GOLD, a * 0.6), 3))                 # хроматическая кромка
            p.drawEllipse(QPointF(cx, cy), rad + 4, rad + 4)
            p.setPen(QPen(_c(CYAN, a), 2))
            p.drawEllipse(QPointF(cx, cy), rad, rad)

    def _flash(self, p, w, h, t):
        if S.T_HIT <= t < S.T_HIT + 0.35:
            p.fillRect(0, 0, w, h, _c(WHITE, 0.55 * (1 - _out((t - S.T_HIT) / 0.35))))

    def node_pos(self, i: int, cx: float, cy: float, r: float) -> tuple[float, float]:
        ang = -math.pi / 2 + 2 * math.pi * i / len(S.NODES)
        return cx + math.cos(ang) * r * 3.25, cy + math.sin(ang) * r * 2.25

    def _nodes(self, p, cx, cy, r, t, exit_k):
        times = S.node_times()
        nr = r * 0.42
        label_f = _font(UI_FONT, r * 0.17, QFont.Weight.DemiBold, 104)
        for i, ((name, icon), at) in enumerate(zip(S.NODES, times)):
            if t < at - 0.25:
                continue
            nx, ny = self.node_pos(i, cx, cy, r)
            # уход в обратном порядке, с разгоном
            back = _in(_clamp((exit_k * 1.6 - (len(S.NODES) - 1 - i) * 0.06) / 0.35), 2)
            ang = math.atan2(ny - cy, nx - cx)
            sx, sy = cx + math.cos(ang) * r * 1.42, cy + math.sin(ang) * r * 1.42
            grow = _out((t - at + 0.25) / 0.25) * (1 - back)
            ex, ey = sx + (nx - sx) * grow, sy + (ny - sy) * grow
            p.setPen(QPen(_c(CYAN, 0.5), 1.3))
            p.drawLine(QPointF(sx, sy), QPointF(ex, ey))
            if grow >= 1:                                         # золотая бусина бежит по линии
                u = ((t - at) * 0.7 + i * 0.13) % 1.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_c(GOLD, 0.9 * (1 - u)))
                p.drawEllipse(QPointF(sx + (nx - sx) * u, sy + (ny - sy) * u), 2.6, 2.6)
            if t < at:
                continue
            pop = _back((t - at) / 0.42) * (1 - back)
            if pop <= 0.01:
                continue
            rad = nr * pop
            c = QPointF(nx, ny)
            # «пинг» — расходящийся круг в момент появления
            pk = _clamp((t - at) / 0.6)
            if pk < 1:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_c(CYAN, 0.6 * (1 - pk)), 1.5))
                p.drawEllipse(c, nr * (1 + 1.4 * _out(pk)), nr * (1 + 1.4 * _out(pk)))
            ok = self.checks.get(_check_for(name), True)
            scan = self._scan_hit(i, t)
            glow = QRadialGradient(c, rad * 2.0)
            glow.setColorAt(0, _c(BLUE, 0.35 + 0.35 * scan))
            glow.setColorAt(1, _c(BLUE, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, rad * 2.0, rad * 2.0)
            glass = QRadialGradient(QPointF(nx - rad * 0.3, ny - rad * 0.35), rad * 1.3)
            glass.setColorAt(0, QColor("#123a6e"))
            glass.setColorAt(1, QColor("#061631"))
            p.setBrush(glass)
            rim = (GREEN if ok else RED) if scan > 0.05 else CYAN
            p.setPen(QPen(_c(rim, 0.95), 1.8))
            p.drawEllipse(c, rad, rad)
            p.setPen(QPen(_c(ICE, 0.25), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, rad * 0.82, rad * 0.82)
            _draw_icon(p, icon, c, rad * 1.25, _c(WHITE, 0.97), _out((t - at - 0.08) / 0.5), max(1.8, rad * 0.07))
            la = _out((t - at - 0.2) / 0.35) * (1 - back)
            if la > 0:
                p.setFont(label_f)
                p.setPen(_c(WHITE, la))
                p.drawText(QRectF(nx - r, ny + rad * 1.25, 2 * r, r * 0.3),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, name)

    def _scan_hit(self, i: int, t: float) -> float:
        """Луч проверки прошёл узел — он вспыхивает (зелёным, если работает)."""
        if t < S.T_NET:
            return 0.0
        ang = (2 * math.pi * i / len(S.NODES))
        passed = S.T_NET + 1.1 * ang / (2 * math.pi)
        return math.exp(-max(0.0, t - passed) / 0.5) if t >= passed else 0.0

    def _scan(self, p, cx, cy, r, t):
        if not (S.T_NET <= t < S.T_NET + 1.2):
            return
        k = (t - S.T_NET) / 1.1
        g = QConicalGradient(QPointF(cx, cy), 90 - 360 * k)
        g.setColorAt(0, _c(CYAN, 0.32))
        g.setColorAt(0.08, _c(CYAN, 0))
        g.setColorAt(1, _c(CYAN, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), r * 3.6, r * 3.6)

    # дуги по всему экрану — «сеть оживает»
    def _arcs_net(self, p, w, h, cx, cy, r, t):
        if t < S.T_NET - 0.4:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        span = max(w, h)
        for k, (ang, reach, bend, delay, ph) in enumerate(self._arcs):
            g = _out((t - S.T_NET + 0.4 - delay - 0.012 * k) / 0.7)
            if g <= 0:
                continue
            x0, y0 = cx + math.cos(ang) * r * 1.5, cy + math.sin(ang) * r * 1.5
            x1, y1 = cx + math.cos(ang) * span * 0.55 * reach, cy + math.sin(ang) * span * 0.42 * reach
            mx, my = (x0 + x1) / 2 - math.sin(ang) * span * 0.18 * bend, (y0 + y1) / 2 + math.cos(ang) * span * 0.18 * bend
            path = QPainterPath(QPointF(x0, y0))
            path.quadTo(QPointF(mx, my), QPointF(x1, y1))
            pen = QPen(_c(CYAN if k % 4 else BLUE, 0.20 + 0.12 * (k % 3 == 0)), 1.0)
            if g < 1:
                total = path.length()
                pen.setDashPattern([max(0.01, total * g), total + 1])
            p.setPen(pen)
            p.drawPath(path)
            if g >= 1:                                            # импульс по дуге
                u = (t * (0.35 + 0.25 * ph) + ph) % 1.0
                pt = path.pointAtPercent(u)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_c(ICE, 0.7 * math.sin(math.pi * u)))
                p.drawEllipse(pt, 2.0, 2.0)
                p.setBrush(_c(CYAN, 0.6))
                p.drawEllipse(QPointF(x1, y1), 2.4, 2.4)
                p.setBrush(Qt.BrushStyle.NoBrush)

    # субтитры: слово за словом, как в образце
    def _subtitles(self, p, w, h, t):
        for at, dur, text in self.lines:
            d = dur if dur else max(0.9, 0.065 * len(text))
            if not (at - 0.05 <= t < at + d + 0.7):
                continue
            a = _out((t - at + 0.05) / 0.15) * (1 - _clamp((t - at - d - 0.3) / 0.4))
            words = S.word_times(text, at, d)
            f = _font(UI_FONT, h * 0.036, QFont.Weight.Bold, 101)
            fm = QFontMetricsF(f)
            space = fm.horizontalAdvance(" ")
            total = sum(fm.horizontalAdvance(wd) for wd, _ in words) + space * (len(words) - 1)
            x = w / 2 - total / 2
            y = h * 0.895
            p.setFont(f)
            for wd, wt in words:
                on = _clamp((t - wt) / 0.12)
                ww = fm.horizontalAdvance(wd)
                p.setPen(_c(QColor("#000000"), 0.55 * a))
                p.drawText(QPointF(x + 1.5, y + 2), wd)
                p.setPen(_c(WHITE, a * (0.38 + 0.62 * on)))
                p.drawText(QPointF(x, y), wd)
                x += ww + space

    def render(self, t: float, w: int = 1280, h: int = 720) -> QImage:
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor("#203040"))                            # «рабочий стол» под интро
        p = QPainter(img)
        self.paint_at(p, w, h, t)
        p.end()
        return img


def _check_for(node: str) -> str:
    """Какая строка проверки относится к узлу (для зелёной/красной вспышки)."""
    return {"Память": "Память", "Telegram": "Telegram", "Звонки": "Telegram", "Интернет": "Gemini"}.get(node, "")


class IntroOverlay(QWidget):
    """Окно поверх всего экрана. play() — показать; finished — интро кончилось или его пропустили."""

    finished = pyqtSignal()

    def __init__(self, checks: dict | None = None, clock=time.monotonic):
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
