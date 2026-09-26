"""Иконки Джарвиса — векторные, в духе SF Symbols (как на iPhone).

Раньше кнопки были символами шрифта (✕ ↑ ← → ⟳ ⤢ ⏮ ⏸ ▶ ⏭ ⏱ ♪ ✓✓): на Windows
они рисовались мутно, разной толщины и местами цветными эмодзи. Здесь каждая
иконка — линии и заливка QPainter: чёткая на любом масштабе экрана.

draw_icon — нарисовать в своём paintEvent; qicon — QIcon для кнопки.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

NAMES = ("play", "pause", "backward", "forward", "expand", "timer", "note", "film",
         "close", "send", "back", "next", "reload", "check", "checks")


def _tri(pts) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in pts])


def draw_icon(p: QPainter, name: str, c: QPointF, s: float, color: QColor):
    """Иконка name с центром c и высотой около s."""
    p.save()
    x, y = c.x(), c.y()
    solid = QPen(color, max(1.0, s * 0.14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                 Qt.PenJoinStyle.RoundJoin)            # толстый круглый шов = скруглённые углы
    line = QPen(color, max(1.2, s * 0.11), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin)
    if name == "play":
        p.setPen(solid)
        p.setBrush(color)
        p.drawPolygon(_tri([(x - s * 0.30, y - s * 0.40), (x - s * 0.30, y + s * 0.40), (x + s * 0.40, y)]))
    elif name == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for dx in (-s * 0.19, s * 0.19):
            p.drawRoundedRect(QRectF(x + dx - s * 0.12, y - s * 0.42, s * 0.24, s * 0.84), s * 0.07, s * 0.07)
    elif name in ("forward", "backward"):
        k = 1 if name == "forward" else -1
        p.setPen(solid)
        p.setBrush(color)
        w, h = s * 0.40, s * 0.30
        for off in (-w, 0.0):
            bx = x + k * off
            p.drawPolygon(_tri([(bx, y - h), (bx, y + h), (bx + k * w, y)]))
    elif name == "expand":                        # две стрелки по диагонали — «развернуть»
        p.setPen(line)
        p.setBrush(Qt.BrushStyle.NoBrush)
        a, t = s * 0.40, s * 0.20
        for k in (1, -1):
            tip = QPointF(x + k * a, y - k * a)
            p.drawLine(QPointF(x + k * a * 0.15, y - k * a * 0.15), tip)
            p.drawLine(tip, QPointF(tip.x() - k * t, tip.y()))
            p.drawLine(tip, QPointF(tip.x(), tip.y() + k * t))
    elif name == "timer":
        p.setPen(line)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = s * 0.38
        cy = y + s * 0.07
        p.drawEllipse(QPointF(x, cy), r, r)
        p.drawLine(QPointF(x, cy), QPointF(x + r * 0.45, cy - r * 0.55))
        p.drawLine(QPointF(x - s * 0.12, y - s * 0.44), QPointF(x + s * 0.12, y - s * 0.44))
    elif name in ("note", "film"):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        if name == "note":                        # восьмая нота
            p.save()
            p.translate(x - s * 0.13, y + s * 0.26)
            p.rotate(-20)
            p.drawEllipse(QPointF(0, 0), s * 0.17, s * 0.12)
            p.restore()
            p.drawRoundedRect(QRectF(x + s * 0.01, y - s * 0.42, s * 0.08, s * 0.68), s * 0.03, s * 0.03)
            flag = QPainterPath(QPointF(x + s * 0.05, y - s * 0.42))
            flag.cubicTo(QPointF(x + s * 0.20, y - s * 0.30), QPointF(x + s * 0.38, y - s * 0.22),
                         QPointF(x + s * 0.26, y - s * 0.02))
            flag.cubicTo(QPointF(x + s * 0.28, y - s * 0.18), QPointF(x + s * 0.18, y - s * 0.22),
                         QPointF(x + s * 0.09, y - s * 0.24))
            flag.closeSubpath()
            p.drawPath(flag)
        else:                                     # экран с треугольником — видео
            p.setPen(line)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(x - s * 0.44, y - s * 0.32, s * 0.88, s * 0.64), s * 0.12, s * 0.12)
            p.setPen(solid)
            p.setBrush(color)
            p.drawPolygon(_tri([(x - s * 0.10, y - s * 0.14), (x - s * 0.10, y + s * 0.14), (x + s * 0.15, y)]))
    elif name == "close":
        p.setPen(line)
        a = s * 0.30
        p.drawLine(QPointF(x - a, y - a), QPointF(x + a, y + a))
        p.drawLine(QPointF(x - a, y + a), QPointF(x + a, y - a))
    elif name == "send":                          # arrow.up
        p.setPen(line)
        p.drawLine(QPointF(x, y + s * 0.36), QPointF(x, y - s * 0.34))
        p.drawLine(QPointF(x, y - s * 0.36), QPointF(x - s * 0.28, y - s * 0.08))
        p.drawLine(QPointF(x, y - s * 0.36), QPointF(x + s * 0.28, y - s * 0.08))
    elif name in ("back", "next"):                # chevron.left / chevron.right
        k = -1 if name == "back" else 1
        p.setPen(line)
        p.drawLine(QPointF(x - k * s * 0.14, y - s * 0.32), QPointF(x + k * s * 0.16, y))
        p.drawLine(QPointF(x + k * s * 0.16, y), QPointF(x - k * s * 0.14, y + s * 0.32))
    elif name == "reload":                        # arrow.clockwise
        p.setPen(line)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = s * 0.32
        p.drawArc(QRectF(x - r, y - r, 2 * r, 2 * r), 100 * 16, 290 * 16)
        tip = QPointF(x + r * 0.17, y - r)
        p.drawLine(tip, QPointF(tip.x() - s * 0.16, tip.y() - s * 0.14))
        p.drawLine(tip, QPointF(tip.x() - s * 0.16, tip.y() + s * 0.14))
    elif name in ("check", "checks"):
        p.setPen(line)
        p.setBrush(Qt.BrushStyle.NoBrush)
        offs = (0.0,) if name == "check" else (-s * 0.14, s * 0.14)
        for dx in offs:
            path = QPainterPath(QPointF(x + dx - s * 0.30, y + s * 0.02))
            path.lineTo(QPointF(x + dx - s * 0.08, y + s * 0.24))
            path.lineTo(QPointF(x + dx + s * 0.32, y - s * 0.24))
            p.drawPath(path)
    p.restore()


def qicon(name: str, size: int, color: str | QColor) -> QIcon:
    """QIcon для кнопки: рисуется с запасом по плотности (чётко на 150 % и 200 %)."""
    scale = 3
    pm = QPixmap(QSize(size * scale, size * scale))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_icon(p, name, QPointF(size * scale / 2, size * scale / 2), size * scale * 0.9, QColor(color))
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)
