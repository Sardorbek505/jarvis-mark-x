"""Интро Джарвиса на весь экран (два хлопка). Сценарий, реплики и звук — core/intro.py.

Лаборатория Старка, а не мультик: почти чёрный экран с тонкой сеткой, в
центре — дуговой реактор (корпус с болтами, десять катушек с обмоткой
зажигаются по кругу, удар — вспыхивает ядро), вокруг — тонкие HUD-кольца.
Модули — линейные иконки в уголках-рамках с подписями и статусом проверки.
Внизу — субтитры того, что говорит Джарвис (слово за словом).

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
                         QPainterPath, QPen, QRadialGradient)
from PyQt6.QtWidgets import QApplication, QWidget

from core import intro as S

CYAN = QColor("#5fd4ff")
ICE = QColor("#cdf3ff")
BLUE = QColor("#2a7fff")
WHITE = QColor("#f4fbff")
GREY = QColor("#6f8396")
GREEN = QColor("#46e8a0")
RED = QColor("#ff4f6a")
NAVY_C = QColor("#0a1c2b")         # чуть светлее в центре — свет реактора на стене
NAVY_E = QColor("#010307")
METAL_HI = QColor("#5b6874")       # корпус реактора: воронёная сталь
METAL_MID = QColor("#1d252d")
METAL_LO = QColor("#080b0f")
PLUS = QPainter.CompositionMode.CompositionMode_Plus       # свет складывается, а не закрашивает
OVER = QPainter.CompositionMode.CompositionMode_SourceOver

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
    """Всё состояние между кадрами — голос и случайные, но постоянные детали."""

    def __init__(self, checks: dict | None = None, now: datetime | None = None):
        self.checks = checks or {}
        self.now = now
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
        # уход: реактор сжимается и улетает наверх, к капсуле
        fly = _in(_clamp((exit_k - 0.25) / 0.6), 2.2)
        ox, oy = cx + (w / 2 - cx) * fly, cy + (h * 0.02 - cy) * fly
        rr = r * (1 - 0.92 * fly)

        hud_a = 1 - _in(_clamp(exit_k / 0.4))
        p.save()
        p.setOpacity(hud_a)
        self._hud(p, w, h, t)
        self._arcs_net(p, w, h, cx, cy, r, t)
        self._rings(p, cx, cy, r, t)
        self._nodes(p, cx, cy, r, t, exit_k)
        self._scan(p, cx, cy, r, t)
        p.restore()
        self._reactor(p, ox, oy, rr, t)
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
        boost = 0.35 * math.exp(-max(0, t - S.T_HIT) / 0.4) * (t >= S.T_HIT)
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
            p.setBrush(_c(ICE, 0.05 * on * s))
            p.drawEllipse(QPointF(x * w, yy * h), s, s)

    def _paint_bg(self, p, w, h, on, boost):
        p.fillRect(0, 0, w, h, NAVY_E)
        g = QRadialGradient(QPointF(w / 2, h * 0.455), max(w, h) * 0.6)
        g.setColorAt(0, _c(NAVY_C.lighter(int(100 + 80 * boost)), on))
        g.setColorAt(0.5, _c(QColor("#050d16"), on))
        g.setColorAt(1, _c(NAVY_E, 0))
        p.fillRect(0, 0, w, h, g)
        # тонкая инженерная сетка — еле видна, гаснет к краям
        step = max(w, h) / 36
        p.setPen(QPen(_c(ICE, 0.035 * on), 1))
        x = (w / 2) % step
        while x < w:
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            x += step
        y = (h * 0.455) % step
        while y < h:
            p.drawLine(QPointF(0, y), QPointF(w, y))
            y += step
        vig = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.7)
        vig.setColorAt(0.45, _c(NAVY_E, 0))
        vig.setColorAt(1, _c(NAVY_E, 0.92))
        p.fillRect(0, 0, w, h, vig)

    # HUD по краям: уголки, часы, загрузка, полоса голоса — по очереди
    def _hud(self, p, w, h, t):
        m = min(w, h) * 0.035
        L = min(w, h) * 0.06
        for k, (x, y, sx, sy) in enumerate(((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1))):
            g = _out((t - 1.0 - 0.09 * k) / 0.45)
            if g <= 0:
                continue
            p.setPen(QPen(_c(ICE, 0.4), 1.0))
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
            pwr = int(round(100 * min(1.0, self._power(t))))
            p.setFont(_font(MONO_FONT, h * 0.018, spacing=110))
            for i, (name, val) in enumerate((("CPU", cpu), ("RAM", ram), ("ARC", pwr))):
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
            p.setPen(QPen(_c(ICE, 0.25 * a), 1))
            p.setBrush(_c(QColor("#03080e"), 0.85 * a))
            p.drawRoundedRect(box, ph / 2, ph / 2)
            lv = self.voice_level(t)
            n = 15
            for i in range(n):
                k = 1 - abs(i - n // 2) / (n / 2)
                amp = 0.18 + 0.82 * lv * k * (0.6 + 0.4 * math.sin(t * 18 + i * 1.7))
                bx = box.left() + pw * (0.2 + 0.6 * i / (n - 1))
                bh = ph * 0.62 * max(0.12, amp)
                p.setPen(QPen(_c(ICE if i == n // 2 else CYAN, 0.9 * a), 2, cap=Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(bx, box.center().y() - bh / 2), QPointF(bx, box.center().y() + bh / 2))

    # ── дуговой реактор ──────────────────────────────────────────────────────
    COILS = 10

    def _power(self, t: float) -> float:
        """Мощность реактора: заряд → «вдох» перед ударом → вспышка → ровный свет (+ голос)."""
        if t < S.T_CHARGE:
            return 0.0
        if t < S.T_HIT:
            base = 0.12 + 0.5 * _out((t - S.T_CHARGE) / (S.T_HIT - S.T_CHARGE), 2)
            inhale = _clamp((t - (S.T_HIT - 0.3)) / 0.3)
            return base * (1 - 0.55 * math.sin(math.pi / 2 * inhale))
        burst = 0.9 * math.exp(-(t - S.T_HIT) / 0.18)
        if t >= S.T_FINAL:
            burst += 0.45 * math.exp(-(t - S.T_FINAL) / 0.4)
        return 1.0 + burst + 0.25 * self.voice_level(t)

    def _coil_power(self, i: int, t: float) -> float:
        """Катушка i зажигается по кругу во время заряда — с электрическим дрожанием при пуске."""
        on_at = S.T_CHARGE + 0.3 + i * (S.T_HIT - S.T_CHARGE - 0.6) / self.COILS
        k = (t - on_at) / 0.14
        if k <= 0:
            return 0.0
        if k < 1:
            return _clamp(k * 1.5) * (0.55 + 0.45 * math.sin(k * 25 + i * 1.7))
        return 1.0

    @staticmethod
    def _sector(r0: float, r1: float, a0: float, span: float) -> QPainterPath:
        """Кольцевой сектор: углы в градусах, против часовой (как у Qt)."""
        path = QPainterPath()
        path.arcMoveTo(QRectF(-r1, -r1, 2 * r1, 2 * r1), a0)
        path.arcTo(QRectF(-r1, -r1, 2 * r1, 2 * r1), a0, span)
        path.arcTo(QRectF(-r0, -r0, 2 * r0, 2 * r0), a0 + span, -span)
        path.closeSubpath()
        return path

    def _reactor(self, p, cx, cy, r, t):
        if t < S.T_CHARGE or r < 1:
            return
        appear = _out((t - S.T_CHARGE) / 0.45)
        pw = self._power(t)
        core = min(1.0, pw)
        R = r * 1.2                                         # внешний край корпуса
        p.save()
        p.translate(cx, cy)
        p.setOpacity(p.opacity() * appear)
        p.setPen(Qt.PenStyle.NoPen)

        # свет на стене вокруг — складывается с фоном
        p.setCompositionMode(PLUS)
        g = QRadialGradient(QPointF(0, 0), R * 3.2)
        glow = min(pw, 1.9)
        g.setColorAt(0, _c(CYAN, 0.2 * glow))
        g.setColorAt(0.22, _c(BLUE, 0.06 * glow))
        g.setColorAt(1, _c(BLUE, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), R * 3.2, R * 3.2)
        p.setCompositionMode(OVER)

        # корпус: воронёная сталь, свет сверху
        housing = QPainterPath()
        housing.addEllipse(QPointF(0, 0), R, R)
        housing.addEllipse(QPointF(0, 0), R * 0.86, R * 0.86)
        lg = QLinearGradient(0, -R, 0, R)
        lg.setColorAt(0, METAL_HI)
        lg.setColorAt(0.45, METAL_MID)
        lg.setColorAt(1, METAL_LO)
        p.setBrush(lg)
        p.drawPath(housing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(ICE, 0.35), max(1.0, R * 0.008)))
        p.drawEllipse(QPointF(0, 0), R, R)
        p.setPen(QPen(_c(QColor("#000000"), 0.7), max(1.0, R * 0.012)))
        p.drawEllipse(QPointF(0, 0), R * 0.86, R * 0.86)
        # болты между катушками
        for i in range(self.COILS):
            a = math.radians(90 + 36 * i)
            b = QPointF(math.cos(a) * R * 0.93, -math.sin(a) * R * 0.93)
            p.setPen(QPen(_c(QColor("#000000"), 0.6), 1))
            p.setBrush(QColor("#7d8a96"))
            p.drawEllipse(b, R * 0.028, R * 0.028)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c(WHITE, 0.55))
            p.drawEllipse(b - QPointF(R * 0.008, R * 0.008), R * 0.009, R * 0.009)

        # катушки: тёмный металл → светятся изнутри, поверх — витки обмотки
        r0, r1 = R * 0.56, R * 0.84
        lit_grad = QRadialGradient(QPointF(0, 0), r1)
        for i in range(self.COILS):
            a0 = 90 - 36 * i - 33                          # по часовой от верха
            sec = self._sector(r0, r1, a0, 30)
            lit = self._coil_power(i, t) * min(1.0, 0.35 + 0.65 * core) if t < S.T_HIT else min(1.0, pw)
            p.setPen(QPen(_c(QColor("#000000"), 0.8), 1))
            p.setBrush(QColor("#11181f"))
            p.drawPath(sec)
            if lit > 0.01:
                lit_grad.setColorAt(r0 / r1, _c(WHITE, 0.95 * lit))
                lit_grad.setColorAt(0.8, _c(ICE, 0.85 * lit))
                lit_grad.setColorAt(1.0, _c(CYAN, 0.55 * lit))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(lit_grad)
                p.drawPath(sec)
            # витки: тени проводов на светящейся катушке, блики — на тёмной
            wire = _c(QColor("#0b2c3c"), 0.65) if lit > 0.3 else _c(METAL_HI, 0.7)
            p.setPen(QPen(wire, max(1.0, R * 0.011)))
            for k in range(1, 9):
                a = math.radians(a0 + 30 * k / 9)
                p.drawLine(QPointF(math.cos(a) * r0 * 1.04, -math.sin(a) * r0 * 1.04),
                           QPointF(math.cos(a) * r1 * 0.97, -math.sin(a) * r1 * 0.97))

        # внутреннее кольцо и вращающаяся шкала
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(ICE, 0.35 + 0.6 * core), max(1.2, R * 0.02)))
        p.drawEllipse(QPointF(0, 0), R * 0.53, R * 0.53)
        p.save()
        p.rotate(t * 24)
        p.setPen(QPen(_c(ICE, 0.45 * core), max(1.0, R * 0.008)))
        for i in range(48):
            a = 2 * math.pi * i / 48
            p.drawLine(QPointF(math.cos(a) * R * 0.45, math.sin(a) * R * 0.45),
                       QPointF(math.cos(a) * R * (0.49 if i % 4 == 0 else 0.475), math.sin(a) * R * (0.49 if i % 4 == 0 else 0.475)))
        p.restore()

        # ядро
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#03111a"))
        p.drawEllipse(QPointF(0, 0), R * 0.43, R * 0.43)
        cg = QRadialGradient(QPointF(0, 0), R * 0.43)
        cg.setColorAt(0, _c(WHITE, core))
        cg.setColorAt(0.3, _c(WHITE, 0.95 * core))
        cg.setColorAt(0.62, _c(ICE, 0.8 * core))
        cg.setColorAt(0.9, _c(CYAN, 0.45 * core))
        cg.setColorAt(1, _c(CYAN, 0.0))
        p.setBrush(cg)
        p.drawEllipse(QPointF(0, 0), R * 0.43, R * 0.43)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(WHITE, 0.35 * core), 1))
        p.drawEllipse(QPointF(0, 0), R * 0.27, R * 0.27)
        # перегрев света на ударе
        if pw > 1.0:
            p.setCompositionMode(PLUS)
            b = QRadialGradient(QPointF(0, 0), R * 1.4)
            b.setColorAt(0, _c(WHITE, 0.55 * (pw - 1.0)))
            b.setColorAt(1, _c(CYAN, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(b)
            p.drawEllipse(QPointF(0, 0), R * 1.4, R * 1.4)
            p.setCompositionMode(OVER)
        p.restore()

    # тонкие HUD-кольца вокруг реактора: появляются после удара и медленно вращаются
    def _rings(self, p, cx, cy, r, t):
        if t < S.T_HIT:
            return
        k = _out((t - S.T_HIT - 0.05) / 0.9)
        p.save()
        p.translate(cx, cy)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r1 = r * 1.55
        p.save()
        p.rotate(t * 10)
        p.setPen(QPen(_c(ICE, 0.5), max(1.5, r * 0.03), cap=Qt.PenCapStyle.FlatCap))
        for a0, sp in ((0, 64), (74, 18), (104, 92), (210, 36), (258, 84)):
            p.drawArc(QRectF(-r1, -r1, 2 * r1, 2 * r1), a0 * 16, int(sp * 16 * k))
        p.restore()
        p.save()
        p.rotate(-t * 6)
        r2 = r * 1.74
        p.setPen(QPen(_c(ICE, 0.3), 1))
        n = 120
        for i in range(int(n * k)):
            a = 2 * math.pi * i / n
            r3 = r2 + (r * 0.1 if i % 10 == 0 else r * 0.045)
            p.drawLine(QPointF(math.cos(a) * r2, math.sin(a) * r2), QPointF(math.cos(a) * r3, math.sin(a) * r3))
        p.restore()
        r4 = r * 2.02
        p.setPen(QPen(_c(ICE, 0.12 * k), 1))
        p.drawEllipse(QPointF(0, 0), r4, r4)
        for ph in (0.0, math.pi):                              # две метки бегут по внешнему кругу
            a = t * 0.7 + ph
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c(ICE, 0.8 * k))
            p.drawEllipse(QPointF(math.cos(a) * r4, math.sin(a) * r4), 2.2, 2.2)
        p.restore()

    def _shockwave(self, p, w, h, cx, cy, r, t):
        for at, power in ((S.T_HIT, 1.0), (S.T_FINAL, 0.55)):
            if not (at <= t < at + 1.0):
                continue
            k = _out((t - at) / 0.9, 2.5)
            rad = r * 1.2 + k * max(w, h) * 0.75 * power
            a = (1 - k) * power
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_c(CYAN, a * 0.08), r * 0.35))          # мягкий фронт
            p.drawEllipse(QPointF(cx, cy), rad, rad)
            p.setPen(QPen(_c(ICE, a * 0.7), 1.2))                # и тонкая кромка
            p.drawEllipse(QPointF(cx, cy), rad, rad)

    def _flash(self, p, w, h, t):
        """Удар: свет реактора на миг заливает комнату — от центра, а не серой плёнкой."""
        if S.T_HIT <= t < S.T_HIT + 0.3:
            k = (1 - _out((t - S.T_HIT) / 0.3)) ** 1.5
            p.save()
            p.setCompositionMode(PLUS)
            g = QRadialGradient(QPointF(w / 2, h * 0.455), max(w, h) * 0.75)
            g.setColorAt(0, _c(WHITE, 0.8 * k))
            g.setColorAt(0.3, _c(ICE, 0.28 * k))
            g.setColorAt(1, _c(CYAN, 0.04 * k))
            p.fillRect(0, 0, w, h, g)
            p.restore()

    def node_pos(self, i: int, cx: float, cy: float, r: float) -> tuple[float, float]:
        ang = -math.pi / 2 + 2 * math.pi * i / len(S.NODES)
        return cx + math.cos(ang) * r * 3.25, cy + math.sin(ang) * r * 2.25

    def _nodes(self, p, cx, cy, r, t, exit_k):
        times = S.node_times()
        s = r * 0.36                                         # половина рамки модуля
        label_f = _font(MONO_FONT, r * 0.13, QFont.Weight.Normal, 118)
        state_f = _font(MONO_FONT, r * 0.1, QFont.Weight.Normal, 125)
        for i, ((name, icon), at) in enumerate(zip(S.NODES, times)):
            if t < at - 0.25:
                continue
            nx, ny = self.node_pos(i, cx, cy, r)
            # уход в обратном порядке, с разгоном
            back = _in(_clamp((exit_k * 1.6 - (len(S.NODES) - 1 - i) * 0.06) / 0.35), 2)
            ang = math.atan2(ny - cy, nx - cx)
            sx, sy = cx + math.cos(ang) * r * 2.02, cy + math.sin(ang) * r * 2.02
            tx, ty = nx - math.cos(ang) * s * 1.25, ny - math.sin(ang) * s * 1.25
            grow = _out((t - at + 0.25) / 0.25) * (1 - back)
            p.setPen(QPen(_c(ICE, 0.25), 1))
            p.drawLine(QPointF(sx, sy), QPointF(sx + (tx - sx) * grow, sy + (ty - sy) * grow))
            if grow >= 1:                                         # импульс данных по линии
                u = ((t - at) * 0.8 + i * 0.13) % 1.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_c(ICE, 0.8 * math.sin(math.pi * u)))
                p.drawEllipse(QPointF(sx + (tx - sx) * u, sy + (ty - sy) * u), 1.6, 1.6)
            if t < at:
                continue
            m = _out((t - at) / 0.3) * (1 - back)
            if m <= 0.01:
                continue
            flick = 1.0 if t - at > 0.22 else 0.45 + 0.55 * (int((t - at) * 45) % 2)   # «включается»
            ok = self.checks.get(_check_for(name), True)
            scan = self._scan_hit(i, t)
            col = (GREEN if ok else RED) if scan > 0.05 else ICE
            c = QPointF(nx, ny)
            box = QRectF(nx - s, ny - s, 2 * s, 2 * s)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_c(QColor("#03070c"), 0.75 * m))
            p.drawRect(box)
            if scan > 0.05:
                p.setBrush(_c(col, 0.14 * scan))
                p.drawRect(box)
            # уголки рамки разъезжаются из центра
            L = s * 0.5
            half = s * (0.4 + 0.6 * m)
            p.setPen(QPen(_c(col, 0.9 * m * flick), max(1.2, r * 0.016), cap=Qt.PenCapStyle.FlatCap))
            for sxn, syn in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                corner = QPointF(nx + sxn * half, ny + syn * half)
                p.drawLine(corner, corner - QPointF(sxn * L, 0))
                p.drawLine(corner, corner - QPointF(0, syn * L))
            _draw_icon(p, icon, c, s * 1.2, _c(WHITE, 0.95 * m * flick), _out((t - at - 0.05) / 0.45),
                       max(1.6, r * 0.02))
            la = _out((t - at - 0.15) / 0.3) * (1 - back)
            if la > 0:
                p.setFont(label_f)
                p.setPen(_c(ICE, 0.85 * la))
                p.drawText(QRectF(nx - r, ny + s * 1.15, 2 * r, r * 0.22),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, name.upper())
                passed = S.T_NET + 1.1 * i / len(S.NODES)
                if t >= passed:
                    p.setFont(state_f)
                    p.setPen(_c(GREEN if ok else RED, 0.8 * la * _out((t - passed) / 0.3)))
                    p.drawText(QRectF(nx - r, ny + s * 1.15 + r * 0.2, 2 * r, r * 0.2),
                               Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                               "В СЕТИ" if ok else "СБОЙ")

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
        g.setColorAt(0, _c(ICE, 0.16))
        g.setColorAt(0.06, _c(CYAN, 0))
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
            x0, y0 = cx + math.cos(ang) * r * 2.1, cy + math.sin(ang) * r * 2.1
            x1, y1 = cx + math.cos(ang) * span * 0.55 * reach, cy + math.sin(ang) * span * 0.42 * reach
            mx, my = (x0 + x1) / 2 - math.sin(ang) * span * 0.18 * bend, (y0 + y1) / 2 + math.cos(ang) * span * 0.18 * bend
            path = QPainterPath(QPointF(x0, y0))
            path.quadTo(QPointF(mx, my), QPointF(x1, y1))
            pen = QPen(_c(CYAN if k % 4 else BLUE, 0.07 + 0.05 * (k % 3 == 0)), 0.8)
            if g < 1:
                total = path.length()
                pen.setDashPattern([max(0.01, total * g), total + 1])
            p.setPen(pen)
            p.drawPath(path)
            if g >= 1:                                            # импульс по дуге
                u = (t * (0.35 + 0.25 * ph) + ph) % 1.0
                pt = path.pointAtPercent(u)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_c(ICE, 0.35 * math.sin(math.pi * u)))
                p.drawEllipse(pt, 1.4, 1.4)
                p.setBrush(Qt.BrushStyle.NoBrush)

    # субтитры: слово за словом, как в образце
    def _subtitles(self, p, w, h, t):
        for at, dur, text in self.lines:
            d = dur if dur else max(0.9, 0.065 * len(text))
            if not (at - 0.05 <= t < at + d + 0.7):
                continue
            a = _out((t - at + 0.05) / 0.15) * (1 - _clamp((t - at - d - 0.3) / 0.4))
            words = S.word_times(text, at, d)
            f = _font(UI_FONT, h * 0.031, QFont.Weight.Medium, 103)
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
                p.setPen(_c(WHITE, a * (0.3 + 0.7 * on)))
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
