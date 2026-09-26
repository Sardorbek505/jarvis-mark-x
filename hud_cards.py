"""Содержимое карточек рядом с шаром: погода, список, плеер, шкала, таймер,
перевод, сообщение, память, приложение, ошибка.

Данные собирает core/result_card.py, рамку и шапку («три точки и адрес»)
рисует HudCanvas в ui.py. Здесь — только тело карточки: сколько ему нужно
высоты и как его нарисовать. `col(alpha, lift)` — цвет текущего состояния
шара, чтобы карточка была в тон ему.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

WHITE, TEXT, MED, DIM, BORDER = "#eef3f6", "#d6dee5", "#8a96a1", "#5c6873", "#151e27"
RED, BLUE, SUN = "#ff4660", "#6fb7ff", "#ffc94a"


def _font(size: float, bold: bool = False, spacing: float = 0.0, mono: bool = False) -> QFont:
    f = QFont("Consolas" if mono else "Segoe UI", int(size),
              QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def _text_h(p: QPainter, text: str, width: float, font: QFont) -> int:
    p.setFont(font)
    return p.fontMetrics().boundingRect(QRectF(0, 0, width, 2000).toRect(),
                                        int(Qt.TextFlag.TextWordWrap), text).height()


def _caps(p: QPainter, rect: QRectF, text: str, color: QColor):
    p.setFont(_font(7, True, 1.4))
    p.setPen(color)
    p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, text.upper())


# ── значки ────────────────────────────────────────────────────────────────────

def weather_icon(p: QPainter, kind: str, cx: float, cy: float, size: float):
    """Значок погоды из простых фигур — без картинок и шрифтов-иконок."""
    s = size / 2
    sun, cloud = QColor(SUN), QColor("#cfd8e0")
    p.save()

    def draw_sun(x, y, r):
        p.setPen(QPen(sun, max(1.5, r * 0.18), cap=Qt.PenCapStyle.RoundCap))
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(x + math.cos(a) * r * 1.35, y + math.sin(a) * r * 1.35),
                       QPointF(x + math.cos(a) * r * 1.75, y + math.sin(a) * r * 1.75))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(sun)
        p.drawEllipse(QPointF(x, y), r, r)

    def draw_cloud(x, y, w, col):
        path = QPainterPath()
        path.addEllipse(QPointF(x - w * 0.28, y + w * 0.05), w * 0.26, w * 0.22)
        path.addEllipse(QPointF(x + w * 0.02, y - w * 0.10), w * 0.32, w * 0.30)
        path.addEllipse(QPointF(x + w * 0.30, y + w * 0.06), w * 0.24, w * 0.20)
        path.addRoundedRect(QRectF(x - w * 0.52, y + w * 0.02, w * 1.04, w * 0.26), w * 0.13, w * 0.13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPath(path.simplified())

    if kind == "sun":
        draw_sun(cx, cy, s * 0.48)
    elif kind == "partly":
        draw_sun(cx - s * 0.25, cy - s * 0.25, s * 0.34)
        draw_cloud(cx + s * 0.12, cy + s * 0.18, s * 1.25, cloud)
    elif kind == "fog":
        p.setPen(QPen(cloud, max(1.5, s * 0.12), cap=Qt.PenCapStyle.RoundCap))
        for i, w in enumerate((0.8, 0.6, 0.75)):
            y = cy - s * 0.35 + i * s * 0.35
            p.drawLine(QPointF(cx - s * w, y), QPointF(cx + s * w, y))
    else:
        dark = kind in ("rain", "storm", "snow")
        draw_cloud(cx, cy - s * 0.18, s * 1.5, QColor("#9aa6b2") if dark else cloud)
        if kind == "rain":
            p.setPen(QPen(QColor(BLUE), max(1.5, s * 0.1), cap=Qt.PenCapStyle.RoundCap))
            for i in range(3):
                x = cx - s * 0.4 + i * s * 0.4
                p.drawLine(QPointF(x, cy + s * 0.35), QPointF(x - s * 0.12, cy + s * 0.7))
        elif kind == "snow":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(WHITE))
            for i in range(3):
                p.drawEllipse(QPointF(cx - s * 0.4 + i * s * 0.4, cy + s * 0.55), s * 0.09, s * 0.09)
        elif kind == "storm":
            bolt = QPainterPath()
            for i, (dx, dy) in enumerate(((0.05, 0.2), (-0.18, 0.58), (0.02, 0.55),
                                          (-0.1, 0.9), (0.25, 0.42), (0.04, 0.45))):
                pt = QPointF(cx + s * dx, cy + s * dy)
                bolt.moveTo(pt) if i == 0 else bolt.lineTo(pt)
            bolt.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(sun)
            p.drawPath(bolt)
    p.restore()


def _temp(v) -> str:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return f"{v}°"
    return f"+{v}°" if v > 0 else f"{v}°"


# ── высота тела карточки ─────────────────────────────────────────────────────

def body_height(p: QPainter, data: dict, width: float) -> float | None:
    kind = data.get("card")
    inner = width - 36
    if kind == "weather":
        return 232.0
    if kind == "list":
        rows = sum(_text_h(p, it, inner - 22, _font(9)) + 10 for it in data.get("items", []))
        return 44.0 + rows + 8
    if kind == "media":
        return 132.0
    if kind == "meter":
        return 118.0
    if kind == "timer":
        return 150.0
    if kind == "translate":
        return 58.0 + _text_h(p, data.get("src", ""), inner, _font(9)) + \
            _text_h(p, data.get("dst", ""), inner, _font(11, True)) + 26
    if kind == "message":
        return 70.0 + _text_h(p, data.get("text", ""), inner * 0.8 - 24, _font(9))
    if kind == "memory":
        return 58.0 + _text_h(p, data.get("value", ""), inner - 42, _font(10))
    if kind == "app":
        return 96.0
    if kind == "error":
        return 54.0 + _text_h(p, data.get("text", ""), inner - 42, _font(9))
    return None


# ── рисование ────────────────────────────────────────────────────────────────

def paint_body(p: QPainter, r: QRectF, data: dict, col, since: float) -> bool:
    """Нарисовать тело. since — сколько секунд карточка на экране. False —
    такого вида нет, пусть HUD нарисует обычный текст."""
    painter = _PAINTERS.get(data.get("card"))
    if not painter:
        return False
    p.save()
    painter(p, r.adjusted(18, 14, -18, -12), data, col, since)
    p.restore()
    return True


def _weather(p, r, wx, col, since):
    x0, y0, w = r.left(), r.top() + 2, r.width()
    weather_icon(p, wx.get("kind", "cloud"), x0 + 32, y0 + 34, 64)
    p.setPen(QColor(WHITE))
    p.setFont(_font(28, True))
    p.drawText(QRectF(x0 + 78, y0, 130, 64), Qt.AlignmentFlag.AlignVCenter, _temp(wx.get("temp")))
    p.setFont(_font(10, True))
    p.setPen(col(235, 0.25))
    p.drawText(QRectF(x0 + 190, y0 + 8, w - 190, 22), Qt.AlignmentFlag.AlignVCenter,
               p.fontMetrics().elidedText(str(wx.get("desc", "")), Qt.TextElideMode.ElideRight, int(w - 190)))
    p.setFont(_font(8))
    p.setPen(QColor(MED))
    p.drawText(QRectF(x0 + 190, y0 + 32, w - 190, 36),
               Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
               f"ощущается {_temp(wx.get('feels'))} · ветер {wx.get('wind', '?')} км/ч"
               f" · влажность {wx.get('humidity', '?')}%")
    days = wx.get("days") or []
    if not days:
        return
    top = y0 + 88
    p.setPen(QPen(QColor(BORDER), 1))
    p.drawLine(QPointF(x0, top - 8), QPointF(x0 + w, top - 8))
    col_w = w / len(days)
    for i, d in enumerate(days):
        cx = x0 + col_w * (i + 0.5)
        if i:
            p.setPen(QPen(QColor(BORDER), 1))
            p.drawLine(QPointF(x0 + col_w * i, top + 4), QPointF(x0 + col_w * i, top + 112))
        p.setFont(_font(7, True, 1.2))
        p.setPen(col(230, 0.2) if i == 1 else QColor(DIM))
        p.drawText(QRectF(cx - col_w / 2, top, col_w, 16), Qt.AlignmentFlag.AlignCenter,
                   str(d.get("name", "")).upper())
        weather_icon(p, d.get("kind", "cloud"), cx, top + 42, 38)
        # максимум — ярко, минимум — тусклее, как в прогнозе на телефоне
        hi, lo = _temp(d.get("max")), _temp(d.get("min"))
        p.setFont(_font(10, True))
        w_hi = p.fontMetrics().horizontalAdvance(hi + "  ")
        p.setFont(_font(10))
        w_lo = p.fontMetrics().horizontalAdvance(lo)
        left = cx - (w_hi + w_lo) / 2
        p.setFont(_font(10, True))
        p.setPen(QColor(WHITE))
        p.drawText(QRectF(left, top + 66, w_hi, 20), Qt.AlignmentFlag.AlignVCenter, hi)
        p.setFont(_font(10))
        p.setPen(QColor(DIM))
        p.drawText(QRectF(left + w_hi, top + 66, w_lo + 2, 20), Qt.AlignmentFlag.AlignVCenter, lo)
        rain = int(d.get("rain", 0) or 0)
        p.setFont(_font(8))
        p.setPen(QColor(BLUE) if rain >= 30 else QColor(DIM))
        p.drawText(QRectF(cx - col_w / 2, top + 88, col_w, 18), Qt.AlignmentFlag.AlignCenter,
                   f"дождь {rain}%" if rain >= 10 else str(d.get("desc", ""))[:16].lower())


def _list(p, r, data, col, since):
    _caps(p, QRectF(r.left(), r.top(), r.width(), 16), data.get("heading", ""), col(230, 0.2))
    y = r.top() + 26
    for i, item in enumerate(data.get("items", [])):
        # пункты появляются по очереди — как будто Джарвис их перечисляет
        k = max(0.0, min(1.0, (since - 0.15 * i) / 0.35))
        if k <= 0:
            break
        p.setOpacity(k)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col(200))
        p.drawEllipse(QPointF(r.left() + 4, y + 8), 2.6, 2.6)
        p.setFont(_font(9))
        h = _text_h(p, item, r.width() - 22, _font(9))
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(r.left() + 16 + (1 - k) * 10, y, r.width() - 22, h + 2),
                   Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, item)
        y += h + 10
    p.setOpacity(1.0)


def _media(p, r, data, col, since):
    # обложка-плитка со значком
    tile = QRectF(r.left(), r.top() + 4, 96, 96)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col(40))
    p.drawRoundedRect(tile, 14, 14)
    p.setPen(QPen(col(120), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(tile, 14, 14)
    c = tile.center()
    if data.get("media") == "film":
        tri = QPainterPath()
        tri.moveTo(c.x() - 14, c.y() - 20)
        tri.lineTo(c.x() + 22, c.y())
        tri.lineTo(c.x() - 14, c.y() + 20)
        tri.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col(245, 0.3))
        p.drawPath(tri)
    else:
        from ui_icons import draw_icon
        draw_icon(p, "note", c, 44, col(245, 0.3))

    x = tile.right() + 18
    w = r.right() - x
    p.setFont(_font(12, True))
    p.setPen(QColor(WHITE))
    title = p.fontMetrics().elidedText(data.get("title", ""), Qt.TextElideMode.ElideRight, int(w))
    p.drawText(QRectF(x, r.top() + 10, w, 24), Qt.AlignmentFlag.AlignVCenter, title)
    p.setFont(_font(8))
    p.setPen(QColor(MED))
    p.drawText(QRectF(x, r.top() + 36, w, 18), Qt.AlignmentFlag.AlignVCenter, data.get("status", ""))
    # живой эквалайзер (или стоп-линия на паузе)
    bars, bw = 16, max(3.0, (w - 15 * 3) / 16)
    base = r.top() + 96
    playing = data.get("playing", True)
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(bars):
        h = 4.0
        if playing:
            h = 6 + 26 * (0.5 + 0.5 * math.sin(since * (5 + i * 0.7) + i * 1.3)) * \
                (0.6 + 0.4 * math.sin(since * 1.7 + i))
        p.setBrush(col(90 + 150 * (h / 32)))
        p.drawRoundedRect(QRectF(x + i * (bw + 3), base - h, bw, h), 1.5, 1.5)


def _icon_meter(p, kind, c, col):
    p.setPen(QPen(col(240, 0.3), 2.2, cap=Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "brightness":
        p.drawEllipse(c, 7, 7)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * 11, c.y() + math.sin(a) * 11),
                       QPointF(c.x() + math.cos(a) * 15, c.y() + math.sin(a) * 15))
    else:
        body = QPainterPath()
        body.moveTo(c.x() - 12, c.y() - 5)
        body.lineTo(c.x() - 6, c.y() - 5)
        body.lineTo(c.x() + 2, c.y() - 12)
        body.lineTo(c.x() + 2, c.y() + 12)
        body.lineTo(c.x() - 6, c.y() + 5)
        body.lineTo(c.x() - 12, c.y() + 5)
        body.closeSubpath()
        p.drawPath(body)
        for rr in (7, 12):
            p.drawArc(QRectF(c.x() + 2 - rr, c.y() - rr, rr * 2, rr * 2), -45 * 16, 90 * 16)


def _meter(p, r, data, col, since):
    level = data.get("level")
    _icon_meter(p, data.get("meter"), QPointF(r.left() + 18, r.top() + 26), col)
    p.setFont(_font(8, True, 1.4))
    p.setPen(QColor(MED))
    p.drawText(QRectF(r.left() + 48, r.top() + 8, 200, 16), Qt.AlignmentFlag.AlignVCenter,
               data.get("label", "").upper())
    p.setFont(_font(24, True))
    p.setPen(QColor(WHITE))
    p.drawText(QRectF(r.left() + 48, r.top() + 22, 200, 40), Qt.AlignmentFlag.AlignVCenter,
               f"{level}%" if level is not None else "—")
    bar = QRectF(r.left(), r.top() + 72, r.width(), 10)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BORDER))
    p.drawRoundedRect(bar, 5, 5)
    if level is not None:
        k = min(1.0, since / 0.5)                        # полоска доезжает до значения
        k = 1 - (1 - k) ** 3
        p.setBrush(col(235, 0.15))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * level / 100 * k, bar.height()), 5, 5)


def _timer(p, r, data, col, since):
    left = max(0.0, float(data.get("due", 0)) - time.time())
    total = max(1.0, float(data.get("total", 1)))
    c = QPointF(r.left() + 60, r.top() + 60)
    rad = 52
    p.setPen(QPen(QColor(BORDER), 6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(c, rad, rad)
    pen = QPen(col(240, 0.2), 6, cap=Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawArc(QRectF(c.x() - rad, c.y() - rad, rad * 2, rad * 2), 90 * 16, int(-360 * 16 * left / total))
    m, sec = divmod(int(left), 60)
    h, m = divmod(m, 60)
    p.setFont(_font(15, True))
    p.setPen(QColor(WHITE))
    p.drawText(QRectF(c.x() - rad, c.y() - 14, rad * 2, 28), Qt.AlignmentFlag.AlignCenter,
               f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}")
    x = r.left() + 136
    _caps(p, QRectF(x, r.top() + 34, r.right() - x, 16), data.get("label", ""), col(230, 0.2))
    p.setFont(_font(9))
    p.setPen(QColor(MED))
    p.drawText(QRectF(x, r.top() + 54, r.right() - x, 40),
               Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
               "Скажите «отмени таймер», чтобы остановить.")


def _translate(p, r, data, col, since):
    y = r.top()
    _caps(p, QRectF(r.left(), y, r.width(), 16), "Исходный текст", QColor(DIM))
    y += 18
    h = _text_h(p, data.get("src", ""), r.width(), _font(9))
    p.setPen(QColor(MED))
    p.drawText(QRectF(r.left(), y, r.width(), h + 2), Qt.TextFlag.TextWordWrap, data.get("src", ""))
    y += h + 12
    lang = data.get("lang") or "Перевод"
    _caps(p, QRectF(r.left(), y, r.width(), 16), f"→ {lang}", col(230, 0.2))
    y += 18
    h = _text_h(p, data.get("dst", ""), r.width(), _font(11, True))
    p.setPen(QColor(WHITE))
    p.drawText(QRectF(r.left(), y, r.width(), h + 2), Qt.TextFlag.TextWordWrap, data.get("dst", ""))


def _message(p, r, data, col, since):
    text = data.get("text", "")
    bw = r.width() * 0.8
    h = _text_h(p, text, bw - 24, _font(9))
    k = 1 - (1 - min(1.0, since / 0.45)) ** 3              # пузырь «улетает» вправо-вверх
    bubble = QRectF(r.right() - bw, r.top() + 4 + (1 - k) * 14, bw, h + 20)
    p.setOpacity(0.35 + 0.65 * k)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col(60))
    p.drawRoundedRect(bubble, 14, 14)
    p.setPen(QColor(WHITE))
    p.drawText(bubble.adjusted(12, 10, -12, -8), Qt.TextFlag.TextWordWrap, text)
    p.setOpacity(1.0)
    from ui_icons import draw_icon
    p.setFont(_font(8))
    p.setPen(col(230, 0.25))
    p.drawText(QRectF(r.left(), bubble.bottom() + 8, r.width() - 24, 18), Qt.AlignmentFlag.AlignRight,
               f"Отправлено в {data.get('to', 'Telegram')}")
    draw_icon(p, "checks", QPointF(r.right() - 9, bubble.bottom() + 17), 14, col(230, 0.25))


def _memory(p, r, data, col, since):
    c = QPointF(r.left() + 14, r.top() + 14)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col(50))
    p.drawEllipse(c, 14, 14)
    p.setPen(QPen(col(240, 0.3), 2.2, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
    tick = QPainterPath()
    tick.moveTo(c.x() - 6, c.y())
    tick.lineTo(c.x() - 1.5, c.y() + 5)
    tick.lineTo(c.x() + 7, c.y() - 5)
    p.drawPath(tick)
    x = r.left() + 40
    heading = "Запомнил" + (f" · {data['key']}" if data.get("key") else "")
    _caps(p, QRectF(x, r.top() + 6, r.right() - x, 16), heading, col(230, 0.2))
    p.setFont(_font(10))
    p.setPen(QColor(WHITE))
    p.drawText(QRectF(x, r.top() + 28, r.right() - x, r.height() - 28),
               Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, data.get("value", ""))


def _app(p, r, data, col, since):
    name = data.get("name", "") or "?"
    tile = QRectF(r.left(), r.top() + 2, 64, 64)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col(55))
    p.drawRoundedRect(tile, 16, 16)
    p.setFont(_font(24, True))
    p.setPen(col(245, 0.35))
    p.drawText(tile, Qt.AlignmentFlag.AlignCenter, name[:1].upper())
    x = tile.right() + 16
    p.setFont(_font(12, True))
    p.setPen(QColor(WHITE))
    p.drawText(QRectF(x, r.top() + 8, r.right() - x, 24), Qt.AlignmentFlag.AlignVCenter,
               p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(r.right() - x)))
    # «загрузка»: бегущая точка, пока не пришёл снимок окна
    p.setFont(_font(8))
    p.setPen(QColor(MED))
    dots = "." * (1 + int(since * 3) % 3)
    p.drawText(QRectF(x, r.top() + 34, r.right() - x, 18), Qt.AlignmentFlag.AlignVCenter,
               f"Открываю{dots}" if since < 2.5 else "Открыто")


def _error(p, r, data, col, since):
    c = QPointF(r.left() + 14, r.top() + 14)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 70, 96, 50))
    p.drawEllipse(c, 14, 14)
    p.setPen(QPen(QColor(RED), 2.2, cap=Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(c.x() - 5, c.y() - 5), QPointF(c.x() + 5, c.y() + 5))
    p.drawLine(QPointF(c.x() + 5, c.y() - 5), QPointF(c.x() - 5, c.y() + 5))
    x = r.left() + 40
    _caps(p, QRectF(x, r.top() + 6, r.right() - x, 16), "Не получилось", QColor(RED))
    p.setFont(_font(9))
    p.setPen(QColor("#e3a2ad"))
    p.drawText(QRectF(x, r.top() + 26, r.right() - x, r.height() - 26),
               Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, data.get("text", ""))


_PAINTERS = {
    "weather": _weather, "list": _list, "media": _media, "meter": _meter,
    "timer": _timer, "translate": _translate, "message": _message,
    "memory": _memory, "app": _app, "error": _error,
}
