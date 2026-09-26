"""
ДЖАРВИС — Голосовой ИИ-ассистент
UI: точная копия Mark-XXXIX с русскоязычными надписями
Требования: pip install PyQt6 psutil Pillow
"""

from __future__ import annotations

import math
import platform
import sys
import threading
import time
from pathlib import Path

import psutil
from PyQt6.QtCore import (
    QPointF, QRectF, Qt,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QKeySequence, QPainter, QPen, QPixmap, QPolygonF,
    QRadialGradient, QShortcut, QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QSizePolicy, QSystemTrayIcon, QTextEdit,
    QVBoxLayout, QWidget,
)

import logging

import numpy as np

import hud_cards
from orb import DotOrb

_logger = logging.getLogger(__name__)

# Fix Windows DPI awareness issue
if platform.system() == "Windows":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(1)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass  # Fail silently on older Windows versions


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W, _MIN_H = 820, 580
_OS = platform.system()


# ─── Цветовая палитра ─────────────────────────────────────────────────────────
# Нейтральная почти чёрная основа: цвет в окне даёт только состояние (шар,
# точка в шапке, рамка чата), как на референсном видео. Имена прежние — ими
# пользуются оверлей настройки и трей.
class C:
    BG       = "#030609"
    PANEL    = "#070c11"
    PANEL2   = "#0a1017"
    BORDER   = "#151e27"
    BORDER_B = "#243240"
    BORDER_A = "#1b2631"
    PRI      = "#3fd0bd"
    PRI_DIM  = "#2a8a7e"
    PRI_GHO  = "#0c1c1b"
    ACC      = "#ff8a34"
    ACC2     = "#d9e25a"
    GREEN    = "#46e880"
    GREEN_D  = "#2aa05a"
    RED      = "#ff4660"
    MUTED_C  = "#ff4660"
    TEXT     = "#d6dee5"
    TEXT_DIM = "#5c6873"
    TEXT_MED = "#8a96a1"
    WHITE    = "#eef3f6"
    DARK     = "#05090d"
    BAR_BG   = "#0f161d"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(a)
    return c


# ─── Системные метрики (CPU / RAM / NET) ──────────────────────────────────────
class _SysMetrics:
    def __init__(self):
        self.cpu = 0.0
        self.mem = 0.0
        self.net = 0.0
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        nc = psutil.net_io_counters()
        now = time.time()
        dt = now - self._last_net_t
        if dt > 0:
            net = ((nc.bytes_sent - self._last_net.bytes_sent) +
                   (nc.bytes_recv - self._last_net.bytes_recv)) / dt / (1024 * 1024)
        else:
            net = 0.0
        self._last_net = nc
        self._last_net_t = now
        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net

    def snapshot(self) -> dict:
        with self._lock:
            return {"cpu": self.cpu, "mem": self.mem, "net": self.net}


_metrics = _SysMetrics()


# ─── Центральный анимированный HUD ────────────────────────────────────────────
# Цвет шара по состоянию. Переход между ними плавный (см. HudCanvas._step).
_STATE_RGB = {
    "ОЖИДАЕТ":          (48, 208, 190),    # бирюзовый — ждёт имени
    "СЛУШАЕТ":          (70, 232, 128),    # зелёный — слушает тебя
    "ДУМАЕТ":           (182, 226, 64),    # жёлто-зелёный — думает / выполняет
    "ОБРАБОТКА":        (182, 226, 64),
    "ГОВОРИТ":          (255, 138, 52),    # оранжевый — говорит
    "ИНИЦИАЛИЗАЦИЯ":    (130, 214, 255),   # голубой реактор — запуск
    "ПЕРЕПОДКЛЮЧЕНИЕ":  (255, 70, 96),     # красный — нет связи
    "ОТКЛЮЧЁН":         (105, 112, 124),   # серый — микрофон выключен
}
_SUB_HOLD_SEC = 6.0      # сколько субтитр висит после последнего слова
# Во что превращается шар, пока идёт команда.
_TOOL_SHAPE = {
    "web_search": "globe", "browser": "globe", "weather": "globe",
    "translation": "globe", "morning_briefing": "globe",
    "music_player": "music", "switch_voice": "music",
    "movie_player": "film", "youtube_player": "screen",
    "look_at_screen": "screen", "look_at_camera": "screen",
    "computer_control": "reactor", "window_control": "reactor", "files": "reactor",
    "sleep_timer": "reactor", "set_mode": "reactor",
}
_SHAPE_HOLD_SEC = 7.0    # фигура держится после команды, пока Джарвис отвечает
_CARD_HOLD_SEC = 14.0    # карточка результата висит после ответа


class HudCanvas(QWidget):
    """Шар из точек, который дышит голосом, меняет цвет по состоянию и
    подписывает снизу, что говорит Джарвис."""

    def __init__(self, face_path: str = "", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Микрофон включён сразу: собственные динамики он больше не слушает
        # (см. speaker_meter.py). Выключить: Ctrl+M.
        self.muted    = False
        self.speaking = False
        self.state    = "ИНИЦИАЛИЗАЦИЯ"
        # Громкость 0..1: микрофон или собственный голос. Атака мгновенная,
        # спад в _step — иначе шар дрожал бы на каждом кадре звука.
        self.level    = 0.0
        # Сколько пикселей снизу занято полем ввода — шар и субтитры выше.
        self.bottom_reserve = 0

        self._orb = DotOrb()
        self._rgb = list(_STATE_RGB["ИНИЦИАЛИЗАЦИЯ"])
        self._ring = 0.0                      # поворот внешнего кольца, градусы
        self._clock = 0.0                     # время анимации, с
        self._last = time.monotonic()

        self._tool: str | None = None
        self._tool_until = 0.0
        self._tool_lock = 0.0                 # 0..1, проявление подписи инструмента

        self._shape_until = 0.0

        # Карточка «что сделал»: {'title', 'address', 'body', 'img'}.
        self._card: dict | None = None
        self._card_t = 0.0
        self._card_vis = 0.0                  # 0..1, проявление карточки
        self._card_born = 0.0

        self._sub_text = ""
        self._sub_t = 0.0
        self._sub_alpha = 0.0

        self._tmr = QTimer(self)
        self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)  # ~60 fps

    # ── вход ─────────────────────────────────────────────────────────────────
    def feed_level(self, value: float):
        self.level = max(self.level, max(0.0, min(1.0, value)))

    def lock_on(self, tool: str, seconds: float = 2.6):
        self._tool = tool
        self._tool_until = time.monotonic() + seconds
        shape = _TOOL_SHAPE.get(tool)
        if shape:
            self._orb.set_shape(shape)
            self._shape_until = time.monotonic() + _SHAPE_HOLD_SEC

    def show_card(self, title: str, address: str, body: str, png: bytes = b"",
                  extra: str = ""):
        data = None
        if extra:
            try:
                import json
                data = json.loads(extra)
                if not isinstance(data, dict) or "card" not in data:
                    data = None
            except ValueError:
                data = None
        img = None
        if png:
            from PyQt6.QtGui import QImage
            img = QImage.fromData(png)
            if img.isNull():
                img = None
        same = self._card and self._card["address"] == address and self._card["title"] == title
        if img is None and same:
            img = self._card.get("img")       # текст пришёл раньше снимка — снимок не теряем
        if not same:
            # Время рождения карточки — для её анимаций. _card_t сдвигается,
            # пока Джарвис говорит (продлевает показ), и анимации от него
            # начинались бы заново.
            self._card_born = time.monotonic()
        self._card = {"title": title, "address": address, "body": body, "img": img, "data": data}
        self._card_t = time.monotonic()

    def set_subtitle(self, text: str):
        text = " ".join((text or "").split())
        if not text:
            return
        self._sub_text = text
        self._sub_t = time.monotonic()

    # ── анимация ─────────────────────────────────────────────────────────────
    def _state_key(self) -> str:
        if self.muted:
            return "ОТКЛЮЧЁН"
        if self.speaking:
            return "ГОВОРИТ"
        return self.state if self.state in _STATE_RGB else "ОЖИДАЕТ"

    def _step(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last)
        self._last = now
        self._clock += dt

        # Своя речь рвётся паузами между словами — спад быстрее, чтобы шар
        # проговаривал слова, а не гудел одним пузырём.
        self.level *= math.exp(-dt * (7.5 if self.speaking else 11.0))
        key = self._state_key()
        busy = key in ("ДУМАЕТ", "ОБРАБОТКА") or self._tool is not None
        # Запуск и переподключение — реактор: Джарвис «заводится».
        if key in ("ИНИЦИАЛИЗАЦИЯ", "ПЕРЕПОДКЛЮЧЕНИЕ"):
            self._orb.set_shape("reactor")
            self._shape_until = now + 1.2
        # Фигура держится, пока идёт работа и ответ, потом точки стекаются
        # обратно в шар.
        if self._orb.shape != "sphere":
            if busy or self.speaking:
                self._shape_until = max(self._shape_until, now + 2.5)
            if now > self._shape_until:
                self._orb.set_shape("sphere")
        self._orb.step(dt, 0.0 if self.muted else self.level, active=busy)

        # Цвет перетекает за ~0.3 с, а не щёлкает.
        k = 1.0 - math.exp(-dt * 7.0)
        tgt = _STATE_RGB[key]
        for i in range(3):
            self._rgb[i] += (tgt[i] - self._rgb[i]) * k

        self._ring = (self._ring + dt * (6.0 + 30.0 * self._orb.energy)) % 360

        if self._tool and now > self._tool_until:
            self._tool = None
        target = 1.0 if self._tool else 0.0
        self._tool_lock += (target - self._tool_lock) * (1.0 - math.exp(-dt * 9.0))

        if self._card:
            if busy or self.speaking:
                self._card_t = max(self._card_t, now - _CARD_HOLD_SEC + 4.0)
            shown = now - self._card_t < _CARD_HOLD_SEC
            rate = 5.0 if shown else 3.0
            self._card_vis += ((1.0 if shown else 0.0) - self._card_vis) * (1.0 - math.exp(-dt * rate))
            if not shown and self._card_vis < 0.01:
                self._card, self._card_vis = None, 0.0

        visible = bool(self._sub_text) and (self.speaking or now - self._sub_t < _SUB_HOLD_SEC)
        rate = 6.0 if visible else 2.5
        self._sub_alpha += ((1.0 if visible else 0.0) - self._sub_alpha) * (1.0 - math.exp(-dt * rate))

        self.update()

    # ── рисование ────────────────────────────────────────────────────────────
    _LAYERS = 7

    def _layers(self, n: int):
        """Слои глубины: постоянные QPolygonF, в память которых пишет numpy.

        Собирать 2400 QPointF из Python каждый кадр — полторы миллисекунды;
        запись координат прямо в буфер полигона — сотые доли. Если буфер
        недоступен (другая сборка PyQt), view=None и полигон строится по-старому.
        """
        cached = getattr(self, "_layer_cache", None)
        if cached and cached[0] == n:
            return cached[1]
        out = []
        for li in range(self._LAYERS):
            lo, hi = n * li // self._LAYERS, n * (li + 1) // self._LAYERS
            poly, view = QPolygonF([QPointF()] * (hi - lo)), None
            try:
                ptr = poly.data()
                ptr.setsize((hi - lo) * 16)
                view = np.frombuffer(ptr, dtype=np.float64).reshape(hi - lo, 2)
            except Exception as exc:
                _logger.debug("QPolygonF без буфера: %s", exc)
            out.append((lo, hi, poly, view))
        self._layer_cache = (n, out)
        return out

    def _paint_card(self, p: QPainter, W: int, H: int, cw: float, k: float):
        """Окошко как мини-браузер: три точки, адресная строка, внутри снимок
        окна или текст результата. Выезжает справа и проявляется."""
        from PyQt6.QtGui import QPainterPath

        card = self._card
        img = card.get("img")
        head = 30.0
        data = card.get("data")
        special = hud_cards.body_height(p, data, cw) if (data and img is None) else None
        if img is not None:
            ch = min(H - 90.0, cw * 0.62 + head)
        elif special:
            ch = min(H - 90.0, head + special)
        else:
            # Высота по тексту: две строки ответа — маленькая карточка, а не
            # пустая коробка.
            p.setFont(QFont("Segoe UI", 9))
            text_h = p.fontMetrics().boundingRect(
                QRectF(0, 0, cw - 32, 1000).toRect(),
                int(Qt.TextFlag.TextWordWrap), card["body"] or "Готово.").height()
            ch = min(H - 90.0, head + 36 + text_h + 18)
        x = W - cw - 16 + (1.0 - k) * 36
        y = 56.0
        rect = QRectF(x, y, cw, ch)
        p.save()
        p.setOpacity(k)

        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        p.fillPath(path, qcol(C.PANEL, 245))
        p.setPen(QPen(self._col(110), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # Шапка: три точки и «адрес».
        for i, col in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(QPointF(x + 16 + i * 14, y + head / 2), 4.2, 4.2)
        bar = QRectF(x + 62, y + 6, cw - 74, head - 12)
        p.setBrush(qcol(C.BG, 220))
        p.drawRoundedRect(bar, 9, 9)
        p.setFont(QFont("Consolas", 8))
        p.setPen(qcol(C.TEXT_MED))
        addr = p.fontMetrics().elidedText(card["address"], Qt.TextElideMode.ElideRight,
                                          int(bar.width() - 20))
        p.drawText(bar.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, addr)

        body = QRectF(x + 1, y + head, cw - 2, ch - head - 1)
        clip = QPainterPath()
        clip.addRoundedRect(body, 11, 11)
        p.setClipPath(clip)
        if special and hud_cards.paint_body(p, body, data, self._col,
                                            time.monotonic() - self._card_born):
            pass
        elif img is not None:
            # Снимок по ширине карточки; длинная страница медленно едет вниз,
            # как прокрутка на видео.
            scale = body.width() / img.width()
            full_h = img.height() * scale
            extra = max(0.0, full_h - body.height())
            off = min(extra, max(0.0, (time.monotonic() - self._card_born - 1.5) * 14.0))
            p.drawImage(QRectF(body.left(), body.top() - off, body.width(), full_h), img)
        else:
            p.setPen(self._col(235, lift=0.2))
            f = QFont("Segoe UI", 9, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
            p.setFont(f)
            p.drawText(body.adjusted(16, 12, -16, 0), Qt.AlignmentFlag.AlignTop,
                       card["title"].upper())
            p.setFont(QFont("Segoe UI", 9))
            p.setPen(qcol(C.TEXT))
            p.drawText(body.adjusted(16, 36, -16, -12),
                       Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       card["body"] or "Готово.")
        p.restore()

    def _glow(self, R: float) -> QPixmap:
        key = (int(R), *(int(c) // 6 for c in self._rgb))
        cached = getattr(self, "_glow_cache", None)
        if cached and cached[0] == key:
            return cached[1]
        size = int(R * 3.8) + 2
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        gp = QPainter(px)
        gp.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QRadialGradient(QPointF(size / 2, size / 2), R * 1.9)
        grad.setColorAt(0.0, self._col(116))
        grad.setColorAt(0.45, self._col(42))
        grad.setColorAt(1.0, self._col(0))
        gp.setPen(Qt.PenStyle.NoPen)
        gp.setBrush(QBrush(grad))
        gp.drawEllipse(QPointF(size / 2, size / 2), R * 1.9, R * 1.9)
        gp.end()
        self._glow_cache = (key, px)
        return px

    def _col(self, alpha: float, lift: float = 0.0) -> QColor:
        """Цвет состояния; lift 0..1 подмешивает белый (ближние точки)."""
        r, g, b = (c + (255 - c) * lift for c in self._rgb)
        return QColor(int(r), int(g), int(b), max(0, min(255, int(alpha))))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), qcol(C.BG))

        e = self._orb.energy
        Hh = max(200, H - self.bottom_reserve)
        fw = min(W, Hh)
        # Карточка справа — шар плавно уступает место влево и чуть сжимается.
        cv = self._card_vis * self._card_vis * (3 - 2 * self._card_vis)
        card_w = min(W * 0.42, 440.0) if W > 560 else 0.0
        R = fw * 0.25 * (1.0 + 0.05 * e) * (1.0 - 0.14 * cv * (card_w > 0))
        cx, cy = W / 2 - cv * card_w * 0.42, Hh / 2 - fw * 0.02 + 10

        # Свечение за шаром — сильнее, когда он говорит. Градиент во весь шар
        # дорог (2+ мс), поэтому он рисуется в картинку один раз на размер и
        # цвет, а громкость меняет только его прозрачность.
        glow = self._glow(R)
        p.setOpacity(min(1.0, (46 + 70 * e) / 116))
        p.drawPixmap(QPointF(cx - glow.width() / 2, cy - glow.height() / 2), glow)
        p.setOpacity(1.0)

        # Тонкое кольцо с делениями и двумя бегущими дугами.
        ring_r = R * 1.34
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(self._col(40), 1))
        p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)
        tick_pen = QPen(self._col(55), 1)
        p.setPen(tick_pen)
        for deg in range(0, 360, 6):
            rad = math.radians(deg + self._ring * 0.25)
            ln = 5 if deg % 30 == 0 else 2.5
            c, s = math.cos(rad), math.sin(rad)
            p.drawLine(QPointF(cx + c * ring_r, cy + s * ring_r),
                       QPointF(cx + c * (ring_r - ln), cy + s * (ring_r - ln)))
        arc_pen = QPen(self._col(150 + 90 * e), 1.6)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc_pen)
        rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
        for base in (self._ring, self._ring + 180):
            p.drawArc(rect, int(-base * 16), int((26 + 30 * e) * 16))

        # Сам шар: дальние точки мельче и темнее, ближние крупнее и светлее.
        # Рисуем пачками по глубине — один вызов на слой, а не на точку.
        xs, ys, zs = self._orb.project(cx, cy, R)
        order = zs.argsort()
        xy = np.column_stack((xs[order], ys[order]))
        layers = self._layers(len(xy))
        for li, (lo, hi, poly, view) in enumerate(layers):
            t = (li + 0.5) / len(layers)               # 0 — дальняя сторона
            pen = QPen(self._col(34 + 221 * t ** 1.3, lift=0.5 * t ** 3),
                       (1.3 + 2.3 * t) * max(0.8, fw / 700))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            # Дальние тусклые слои без сглаживания: разницы не видно, а это
            # почти половина времени кадра.
            p.setRenderHint(QPainter.RenderHint.Antialiasing, li >= 3)
            if view is not None:
                view[:] = xy[lo:hi]
            else:
                poly = QPolygonF([QPointF(x, y) for x, y in xy[lo:hi].tolist()])
            p.drawPoints(poly)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Плашка статуса сверху.
        key = self._state_key()
        label = {"ОЖИДАЕТ": "ЖДЁТ «ДЖАРВИС»"}.get(key, key)
        f = QFont("Segoe UI", 8, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(label) + 34
        pill = QRectF(cx - tw / 2, 14, tw, 24)
        p.setPen(QPen(self._col(90), 1))
        p.setBrush(QBrush(self._col(22)))
        p.drawRoundedRect(pill, 12, 12)
        pulse = 0.55 + 0.45 * math.sin(self._clock * 3.2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._col(120 + 135 * pulse)))
        p.drawEllipse(QPointF(pill.left() + 14, pill.center().y()), 3.2, 3.2)
        p.setPen(QPen(self._col(235, lift=0.25), 1))
        p.drawText(pill.adjusted(22, 0, -8, 0), Qt.AlignmentFlag.AlignCenter, label)

        # Подпись инструмента, который сейчас выполняется.
        if self._tool_lock > 0.02 and self._tool:
            p.setFont(QFont("Consolas", 9))
            p.setPen(QPen(self._col(220 * self._tool_lock, lift=0.2), 1))
            p.drawText(QRectF(cx - 200, pill.bottom() + 6, 400, 18), Qt.AlignmentFlag.AlignCenter,
                       "▸ " + self._tool.replace("_", " ").upper())

        if self._card and cv > 0.01 and card_w > 0:
            self._paint_card(p, W, H, card_w, cv)

        # Субтитры: то, что Джарвис говорит сейчас. Длинный ответ — хвост.
        if self._sub_alpha > 0.02 and self._sub_text:
            text = self._sub_text
            if len(text) > 150:
                text = "…" + text[-150:].split(" ", 1)[-1]
            p.setFont(QFont("Segoe UI", max(10, int(fw / 52))))
            bw = min(W * 0.84, 640)
            top = cy + ring_r + fw * 0.05
            p.setPen(QPen(QColor(236, 242, 246, int(235 * self._sub_alpha)), 1))
            p.drawText(QRectF(cx - bw / 2, top, bw, max(20, Hh - top)),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                       | Qt.TextFlag.TextWordWrap, text)


# ─── Шапка: имя, цифры, часы ──────────────────────────────────────────────────
class HeaderBar(QWidget):
    """Тонкая строка сверху: слева имя с точкой цвета состояния, по центру
    цифры системы мелким моноширинным, справа часы. Кнопка микрофона
    добавляется в неё снаружи (layout)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self._rgb = (63, 208, 189)
        self._stats: list[tuple[str, str]] = []
        self._pulse = 0.0
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(50)

    def set_accent(self, rgb):
        self._rgb = tuple(int(c) for c in rgb)
        self.update()

    def set_stats(self, stats: list[tuple[str, str]]):
        self._stats = stats
        self.update()

    def _tick(self):
        self._pulse = (self._pulse + 0.05 * 3.2) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), qcol(C.BG))
        p.setPen(QPen(qcol(C.BORDER), 1))
        p.drawLine(0, H - 1, W, H - 1)

        r, g, b = self._rgb
        k = 0.55 + 0.45 * math.sin(self._pulse)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(r, g, b, int(60 * k)))
        p.drawEllipse(QPointF(18, H / 2), 7, 7)
        p.setBrush(QColor(r, g, b))
        p.drawEllipse(QPointF(18, H / 2), 3.5, 3.5)

        f = QFont("Segoe UI", 9, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.5)
        p.setFont(f)
        p.setPen(qcol(C.WHITE))
        p.drawText(QRectF(32, 0, 160, H), Qt.AlignmentFlag.AlignVCenter, "JARVIS")
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(qcol(C.TEXT_DIM))
        p.drawText(QRectF(103, 0, 60, H), Qt.AlignmentFlag.AlignVCenter, "MARK X")

        # Цифры — «ЦПУ 12%  ·  ОЗУ 48%  ·  …», подпись тусклая, значение светлое.
        lab_f, val_f = QFont("Consolas", 7), QFont("Consolas", 8, QFont.Weight.Bold)
        parts = []
        for lab, val in self._stats:
            p.setFont(lab_f)
            lw = p.fontMetrics().horizontalAdvance(lab + " ")
            p.setFont(val_f)
            vw = p.fontMetrics().horizontalAdvance(val)
            parts.append((lab, val, lw, vw))
        gap = 22
        total = sum(lw + vw for _, _, lw, vw in parts) + gap * max(0, len(parts) - 1)
        x = (W - total) / 2
        for i, (lab, val, lw, vw) in enumerate(parts):
            p.setFont(lab_f)
            p.setPen(qcol(C.TEXT_DIM))
            p.drawText(QRectF(x, 0, lw, H), Qt.AlignmentFlag.AlignVCenter, lab)
            p.setFont(val_f)
            p.setPen(qcol(C.TEXT_MED))
            p.drawText(QRectF(x + lw, 0, vw + 2, H), Qt.AlignmentFlag.AlignVCenter, val)
            x += lw + vw + gap
            if i < len(parts) - 1:
                p.setPen(qcol(C.BORDER_B))
                p.drawText(QRectF(x - gap, 0, gap, H), Qt.AlignmentFlag.AlignCenter, "·")


# ─── Лог-виджет диалога (HUD Chat) ──────────────────────────────────────────
class LogWidget(QTextEdit):
    """Высокотехнологичный виджет диалога со стилями Stark HUD и мгновенным выводом."""
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(True)
        self.setFont(QFont("Segoe UI", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background-color: transparent;
                color: {C.TEXT};
                border: none;
                padding: 2px 10px 6px 12px;
                selection-background-color: {C.BORDER_B};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 2px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {C.PRI_DIM};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self._sig.connect(self._handle_append)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _para(self, top: int, runs):
        from PyQt6.QtGui import QTextBlockFormat, QTextCharFormat
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        bf = QTextBlockFormat()
        bf.setTopMargin(top)
        if self.document().isEmpty():
            cur.setBlockFormat(bf)
        else:
            cur.insertBlock(bf)
        for txt, col, size, bold, spacing in runs:
            cf = QTextCharFormat()
            cf.setForeground(qcol(col))
            f = QFont("Segoe UI", size, QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
            if spacing:
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
            cf.setFont(f)
            cur.insertText(txt, cf)
        self.setTextCursor(cur)

    def _handle_append(self, text: str):
        if not text:
            return

        tl = text.strip().lower()
        now_str = time.strftime("%H:%M")
        body = text.split(":", 1)[1].strip() if ":" in text else text

        # Каждая строка — свой абзац через курсор. insertHtml склеивал новый
        # <p> с концом предыдущего: реплики шли одной строкой.
        if tl.startswith("вы:") or tl.startswith("you:"):
            self._para(12, [("ВЫ", C.TEXT_MED, 8, True, 1.2), ("   " + now_str, C.TEXT_DIM, 8, False, 0)])
            self._para(3, [(body, C.WHITE, 10, False, 0)])
        elif tl.startswith("джарвис:") or tl.startswith("jarvis:"):
            self._para(12, [("ДЖАРВИС", C.ACC, 8, True, 1.2), ("   " + now_str, C.TEXT_DIM, 8, False, 0)])
            self._para(3, [(body, C.TEXT, 10, False, 0)])
        elif tl.startswith("err:") or "ошибка" in tl:
            self._para(6, [("✕  ", C.RED, 8, True, 0), (body, "#d98a96", 8, False, 0)])
        elif tl.startswith("sys:"):
            self._para(6, [("·  " + body, C.TEXT_DIM, 8, False, 0)])
        else:
            self._para(6, [("·  " + text, C.TEXT_DIM, 8, False, 0)])

        self.ensureCursorVisible()
        sb = self.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())


# ─── Экран настройки ──────────────────────────────────────────────────────────
class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None, reason="init"):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(_OS.lower(), "linux")
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def lbl(txt, size=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        if reason == "invalid":
            layout.addWidget(lbl("◈ НЕДЕЙСТВИТЕЛЬНЫЙ API КЛЮЧ", 13, True))
            layout.addWidget(lbl("Ваш ключ недействителен. Введите новый ключ.", 9, color=C.PRI_DIM))
        else:
            layout.addWidget(lbl("◈ ТРЕБУЕТСЯ ИНИЦИАЛИЗАЦИЯ", 13, True))
            layout.addWidget(lbl("Настройте ДЖАРВИС перед первым запуском.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(lbl("GEMINI API КЛЮЧ", 8, color=C.TEXT_DIM,
                              align=Qt.AlignmentFlag.AlignLeft))

        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…  (получить на aistudio.google.com)")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(lbl("ОПЕРАЦИОННАЯ СИСТЕМА", 8, color=C.TEXT_DIM,
                              align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(lbl(f"Определено автоматически: {det_name}", 8, color=C.ACC2,
                              align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout()
        os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows", "⊞ Windows"), ("mac", " macOS"), ("linux", "🐧 Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)

        layout.addSpacing(12)

        init_btn = QPushButton("▸ ИНИЦИАЛИЗИРОВАТЬ СИСТЕМЫ")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {
            "windows": (C.PRI, "#001a22"),
            "mac":     (C.ACC2, "#1a1400"),
            "linux":   (C.GREEN, "#001a0d"),
        }
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() + f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


# ─── Главное окно ─────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    # Громкость приходит из аудио-потока, имя инструмента — из событийного
    # цикла. Оба чужие для Qt, поэтому только через сигналы: трогать виджеты
    # из другого потока — это падение, а не подтормаживание.
    _level_sig = pyqtSignal(float)
    _tool_sig  = pyqtSignal(str)
    _sub_sig   = pyqtSignal(str)
    _card_sig  = pyqtSignal(str, str, str, bytes, str)
    # Глобальные хоткеи приходят из потока Win32-сообщений — тоже чужого.
    _mute_sig  = pyqtSignal()
    _front_sig = pyqtSignal()
    # wait_for_api_key зовётся из рабочего потока: оверлей — только сигналом.
    _overlay_sig = pyqtSignal(str)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("Д.Ж.А.Р.В.И.С — Голосовой ИИ")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        # Установка иконки окна и панели задач
        try:
            from PyQt6.QtGui import QIcon
            from core.paths import get_base_dir, get_app_dir
            for ico_candidate in [
                get_base_dir() / "app.ico",
                get_app_dir() / "app.ico",
                Path(__file__).resolve().parent / "app.ico",
                get_base_dir() / "face.png",
            ]:
                if ico_candidate.exists():
                    self.setWindowIcon(QIcon(str(ico_candidate)))
                    break
        except Exception as exc:
            _logger.debug("Window icon setup error: %s", exc)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {C.BG}; color: {C.TEXT}; }}
            QSplitter::handle {{ background: {C.BORDER}; }}
        """)

        self.muted = False        # см. комментарий выше — микрофон слушает сразу
        self.current_file: str | None = None
        self.on_text_command = None

        # ── Системный трей Windows ──────────────────────────────────
        try:
            from core.tray import JarvisTray
            self.tray = JarvisTray(main_window=self, on_exit=self.force_quit, parent=self)
            self.tray.show()
        except Exception as _exc:
            _logger.debug("Системный трей недоступен: %s", _exc)
            self.tray = None

        # ── Центральный виджет ──────────────────────────────────────
        # Шапка во всю ширину, под ней шар и чат. Левой колонки с полосками
        # больше нет: цифры уехали в шапку, статус — в плашку над шаром.
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 12, 0)
        head_row.setSpacing(8)
        self._header = HeaderBar()
        head_row.addWidget(self._header, stretch=1)
        self._clock = QLabel()
        self._clock.setFont(QFont("Consolas", 8))
        self._clock.setStyleSheet(f"color: {C.TEXT_DIM}; background: {C.BG};")
        head_row.addWidget(self._clock)
        self._chat_btn = QPushButton("≡  ДИАЛОГ")
        self._chat_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._chat_btn.setFixedHeight(24)
        self._chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chat_btn.setToolTip("История диалога (Ctrl+J)")
        self._chat_btn.clicked.connect(lambda: self._show_chat(not self._chat_open))
        head_row.addWidget(self._chat_btn)
        self._mute_btn = QPushButton()
        self._mute_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._mute_btn.setFixedHeight(24)
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.setToolTip("Микрофон (Ctrl+M)")
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        head_row.addWidget(self._mute_btn)
        head_wrap = QWidget()
        head_wrap.setStyleSheet(f"background: {C.BG}; border-bottom: 1px solid {C.BORDER};")
        head_wrap.setLayout(head_row)
        outer.addWidget(head_wrap)

        # ── Центральный HUD — на всю ширину, как на видео ────────────
        self._hud = HudCanvas(face_path)
        self._hud.bottom_reserve = 72           # место под поле ввода
        outer.addWidget(self._hud, stretch=1)
        self._hud.installEventFilter(self)

        # ── История диалога: выезжает справа поверх шара ─────────────
        # Постоянная колонка чата отнимала треть экрана, а сказанное и так
        # видно в субтитрах. Теперь история — по кнопке / Ctrl+J и прячется
        # сама, когда ею не пользуются.
        self._chat = QFrame(self._hud)
        self._chat.setObjectName("chat")
        chat_lay = QVBoxLayout(self._chat)
        chat_lay.setContentsMargins(0, 10, 0, 10)
        chat_lay.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(14, 0, 8, 0)
        title = QLabel("ДИАЛОГ")
        tf = QFont("Segoe UI", 7, QFont.Weight.Bold)
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.5)
        title.setFont(tf)
        title.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        top.addWidget(title)
        top.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Скрыть (Esc)")
        close_btn.clicked.connect(lambda: self._show_chat(False))
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: none; }}
            QPushButton:hover {{ color: {C.WHITE}; }}
        """)
        top.addWidget(close_btn)
        chat_lay.addLayout(top)

        self._log = LogWidget()
        chat_lay.addWidget(self._log, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(14, 0, 14, 0)
        btn_row.setSpacing(14)
        for label, slot in [("Очистить", self._clear_log), ("Свернуть в трей", self.close)]:
            b = QPushButton(label)
            b.setFont(QFont("Segoe UI", 7))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: none; padding: 0; }}
                QPushButton:hover {{ color: {C.TEXT}; }}
            """)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        btn_row.addStretch()
        chat_lay.addLayout(btn_row)

        self._chat_open = False
        self._chat_touched = 0.0
        self._unread = False
        self._style_chat_btn()
        from PyQt6.QtCore import QEasingCurve, QPropertyAnimation
        self._chat_anim = QPropertyAnimation(self._chat, b"pos", self)
        self._chat_anim.setDuration(280)
        self._chat_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # ── Поле ввода: «таблетка» внизу по центру ─────────────────
        # Узкая в покое, расширяется, когда в ней печатают. Начать печатать
        # можно откуда угодно в окне — буква сама уходит в поле.
        self._pill = QFrame(self._hud)
        self._pill.setObjectName("pill")
        pill_lay = QHBoxLayout(self._pill)
        pill_lay.setContentsMargins(16, 4, 4, 4)
        pill_lay.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Напишите Джарвису…")
        self._input.setFont(QFont("Segoe UI", 9))
        self._input.setStyleSheet(f"""
            QLineEdit {{ background: transparent; color: {C.TEXT}; border: none; }}
        """)
        self._input.returnPressed.connect(self._send_text)
        self._input.installEventFilter(self)
        pill_lay.addWidget(self._input)
        send_btn = QPushButton("↑")
        send_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        send_btn.setFixedSize(30, 30)
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setToolTip("Отправить (Enter)")
        send_btn.clicked.connect(self._send_text)
        send_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.WHITE}; color: {C.BG}; border: none; border-radius: 15px; }}
            QPushButton:hover {{ background: #ffffff; }}
        """)
        pill_lay.addWidget(send_btn)
        self._pill_anim = QPropertyAnimation(self._pill, b"geometry", self)
        self._pill_anim.setDuration(220)
        self._pill_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._style_pill(False)

        self._chat_timer = QTimer(self)
        self._chat_timer.timeout.connect(self._chat_autohide)
        self._chat_timer.start(1000)

        self._chat_accent((63, 208, 189))
        self._started = time.monotonic()
        self._tools_run = 0
        self._place_overlays()

        # ── Оверлей настройки (поверх всего) ───────────────────────
        self._overlay = None
        self._setup_done = False

        # ── Горячие клавиши ─────────────────────────────────────────
        QShortcut(QKeySequence("Ctrl+M"), self).activated.connect(self._toggle_mute)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self._clear_log)
        QShortcut(QKeySequence("Ctrl+J"), self).activated.connect(
            lambda: self._show_chat(not self._chat_open))
        QShortcut(QKeySequence("Esc"), self).activated.connect(self._on_escape)

        # ── Таймеры ─────────────────────────────────────────────────
        self._metric_timer = QTimer(self)
        self._metric_timer.timeout.connect(self._update_metrics)
        self._metric_timer.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._log_sig.connect(self._mark_unread)
        self._state_sig.connect(self._apply_state)
        self._level_sig.connect(self._hud.feed_level)
        self._tool_sig.connect(self._hud.lock_on)
        self._tool_sig.connect(self._count_tool)
        self._sub_sig.connect(self._hud.set_subtitle)
        self._card_sig.connect(self._hud.show_card)
        self._mute_sig.connect(self._toggle_mute)
        self._front_sig.connect(self._bring_to_front)
        self._overlay_sig.connect(self._show_overlay)

    # ── Публичный API ──────────────────────────────────────────────────────────
    def write_log(self, text: str):
        self._log_sig.emit(text)
        # Готовый ответ (в том числе на текстовую команду) — ещё и субтитром.
        if text[:8].lower() == "джарвис:":
            self._sub_sig.emit(text.split(":", 1)[1])

    def set_subtitle(self, text: str):
        """Субтитр под шаром — то, что Джарвис произносит. Из любого потока."""
        self._sub_sig.emit(str(text))

    def show_card(self, title: str, address: str, body: str, png: bytes = b"",
                  extra: str = ""):
        """Карточка результата рядом с шаром. Из любого потока."""
        self._card_sig.emit(str(title), str(address), str(body), bytes(png or b""),
                            str(extra or ""))

    def set_state(self, state: str):
        self._state_sig.emit(state)

    def set_level(self, value: float):
        """Громкость 0..1 — ею дышит весь HUD. Зовётся из аудио-потока."""
        self._level_sig.emit(float(value))

    def lock_on(self, tool: str):
        """Подписать над шаром инструмент, который сейчас выполняется."""
        self._tool_sig.emit(str(tool))

    def wait_for_api_key(self):
        """Блокирует поток до получения API-ключа. Пропускает если ключ уже есть."""
        import threading
        from core.paths import load_api_keys

        # Проверяем наличие ключа во всех конфигурациях (%APPDATA% и локально)
        import os
        keys = load_api_keys()
        api_key = (os.getenv("GEMINI_API_KEY") or keys.get("gemini_api_key", "")).strip()
        if api_key:
            print("[UI] API ключ найден, пропускаем инициализацию...")
            return None

        # Ключа нет или файл повреждён — показываем оверлей
        reason = "init"
        self._key_ready = threading.Event()
        self._setup_reason = reason
        # QTimer.singleShot из этого потока не срабатывал: оверлей не
        # появлялся, а поток навсегда засыпал на wait() — окно есть, Джарвиса нет.
        self._overlay_sig.emit(reason)
        self._key_ready.wait()
        return reason

    def _show_overlay(self, reason="init"):
        self._overlay = SetupOverlay(self.centralWidget(), reason=reason)
        self._overlay.done.connect(self._on_setup_done)
        self._resize_overlay()
        self._overlay.show()
        self._overlay.raise_()

    def _resize_overlay(self):
        if self._overlay:
            self._overlay.setGeometry(
                self.width() // 2 - 240,
                self.height() // 2 - 220,
                480, 420,
            )

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._resize_overlay()

    def _on_setup_done(self, key: str, os_name: str):
        from core.paths import save_api_keys
        save_api_keys({"gemini_api_key": key, "os": os_name})

        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._setup_done = True
        if hasattr(self, "_key_ready"):
            self._key_ready.set()

    def _apply_state(self, state: str):
        state_map = {
            "IDLE":       "ОЖИДАЕТ",
            "LISTENING":  "СЛУШАЕТ",
            "THINKING":   "ДУМАЕТ",
            "SPEAKING":   "ГОВОРИТ",
            "PROCESSING": "ОБРАБОТКА",
            "INITIALISING": "ИНИЦИАЛИЗАЦИЯ",
            "RECONNECTING": "ПЕРЕПОДКЛЮЧЕНИЕ",
        }
        ru = state_map.get(state.upper(), state)
        self._hud.state = ru
        self._hud.speaking = (state.upper() == "SPEAKING")
        self._chat_accent(_STATE_RGB.get("ОТКЛЮЧЁН" if self.muted else ru, _STATE_RGB["ОЖИДАЕТ"]))

    def _toggle_mute(self):
        self.muted = not self.muted
        self._hud.muted = self.muted
        self._style_mute_btn()
        if self.muted:
            self.write_log("SYS: Микрофон отключён.")
            self._hud.state = "ОТКЛЮЧЁН"
        else:
            self.write_log("SYS: Микрофон включён.")
            self._hud.state = "СЛУШАЕТ"
        self._chat_accent(_STATE_RGB[self._hud.state])

    def _style_mute_btn(self):
        if self.muted:
            self._mute_btn.setText("●  МИКРОФОН ВЫКЛ")
            col, border = C.RED, "rgba(255, 70, 96, 110)"
        else:
            self._mute_btn.setText("●  МИКРОФОН")
            col, border = C.TEXT_MED, C.BORDER_B
        self._mute_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {col};
                border: 1px solid {border}; border-radius: 12px; padding: 0px 12px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.TEXT_DIM}; }}
        """)

    def _count_tool(self, _name: str):
        self._tools_run += 1

    def _send_text(self):
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.write_log(f"Вы: {text}")
        if callable(self.on_text_command):
            self.on_text_command(text)

    def _clear_log(self):
        self._log.clear()

    def _on_file(self, path: str):
        self.current_file = path
        self.write_log(f"FILE: Загружен → {Path(path).name}")

    def _update_metrics(self):
        snap = _metrics.snapshot()
        net = snap["net"]
        net_str = f"{net:.1f}МБ/с" if net >= 0.1 else f"{net*1024:.0f}КБ/с"
        up = int(time.monotonic() - self._started)
        self._header.set_stats([
            ("ЦПУ", f"{snap['cpu']:.0f}%"),
            ("ОЗУ", f"{snap['mem']:.0f}%"),
            ("СЕТЬ", net_str),
            ("КОМАНД", str(self._tools_run)),
            ("В СЕТИ", f"{up // 3600:02d}:{up % 3600 // 60:02d}"),
        ])
        self._clock.setText(time.strftime("%H:%M"))

    # ── история и поле ввода ───────────────────────────────────────────────
    _CHAT_W = 380

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if not hasattr(self, "_pill"):          # окно ещё собирается
            return super().eventFilter(obj, ev)
        if obj is self._hud and ev.type() == QEvent.Type.Resize:
            self._place_overlays()
        elif obj is self._input and ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._style_pill(ev.type() == QEvent.Type.FocusIn, animate=True)
        return super().eventFilter(obj, ev)

    def _chat_rect(self, open_: bool) -> QRectF:
        W, H = self._hud.width(), self._hud.height()
        w = min(self._CHAT_W, max(260, W - 40))
        x = W - w - 12 if open_ else W + 8
        return QRectF(x, 12, w, max(200, H - 24 - self._hud.bottom_reserve))

    def _pill_rect(self, wide: bool) -> QRectF:
        W, H = self._hud.width(), self._hud.height()
        w = min(W - 40, 560 if wide else 360)
        return QRectF((W - w) / 2, H - 56, w, 40)

    def _place_overlays(self):
        r = self._chat_rect(self._chat_open)
        self._chat_anim.stop()
        self._chat.setGeometry(r.toRect())
        self._pill_anim.stop()
        self._pill.setGeometry(self._pill_rect(self._input.hasFocus()).toRect())
        self._chat.raise_()
        self._pill.raise_()

    def _show_chat(self, open_: bool):
        if open_ == self._chat_open:
            return
        self._chat_open = open_
        self._chat_touched = time.monotonic()
        if open_:
            self._unread = False
            self._style_chat_btn()
        self._chat_anim.stop()
        self._chat_anim.setStartValue(self._chat.pos())
        self._chat_anim.setEndValue(self._chat_rect(open_).topLeft().toPoint())
        self._chat_anim.start()

    def _chat_autohide(self):
        if not self._chat_open:
            return
        now = time.monotonic()
        if self._chat.underMouse() or self._input.hasFocus():
            self._chat_touched = now
        elif now - self._chat_touched > 12.0:
            self._show_chat(False)

    def _mark_unread(self, text: str):
        if not self._chat_open and text[:4].lower() in ("вы: ", "джар"):
            self._unread = True
            self._style_chat_btn()

    def _style_chat_btn(self):
        dot = "  ●" if self._unread else ""
        self._chat_btn.setText(f"≡  ДИАЛОГ{dot}")
        col = C.WHITE if self._unread else C.TEXT_MED
        self._chat_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {col};
                border: 1px solid {C.BORDER_B}; border-radius: 12px; padding: 0px 12px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.TEXT_DIM}; }}
        """)

    def _style_pill(self, focused: bool, animate: bool = False):
        border = C.BORDER_B if focused else C.BORDER
        self._pill.setStyleSheet(f"""
            QFrame#pill {{
                background: rgba(10, 16, 23, 235);
                border: 1px solid {border}; border-radius: 20px;
            }}
        """)
        if animate:
            self._pill_anim.stop()
            self._pill_anim.setStartValue(self._pill.geometry())
            self._pill_anim.setEndValue(self._pill_rect(focused).toRect())
            self._pill_anim.start()

    def _on_escape(self):
        if self._chat_open:
            self._show_chat(False)
        elif self._input.hasFocus():
            self._input.clearFocus()

    def keyPressEvent(self, e):
        # Начал печатать где угодно — буква уходит в поле ввода.
        txt = e.text()
        if txt and txt.isprintable() and not self._input.hasFocus() and \
                not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            self._input.setFocus()
            self._input.insert(txt)
            return
        super().keyPressEvent(e)

    def _chat_accent(self, rgb):
        """Рамка чата и точка в шапке — цвета состояния, приглушённо."""
        r, g, b = rgb
        self._chat.setStyleSheet(f"""
            QFrame#chat {{
                background: rgba(7, 12, 17, 240);
                border: 1px solid rgba({r}, {g}, {b}, 70);
                border-radius: 12px;
            }}
        """)
        self._header.set_accent(rgb)

    def closeEvent(self, event):
        """Сворачивание в трей при закрытии окна (вместо уничтожения процесса)."""
        if hasattr(self, "tray") and self.tray and self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "JARVIS Mark X",
                "Ассистент свёрнут в системный трей и продолжает слушать.",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
        else:
            event.accept()

    def force_quit(self):
        """Полное закрытие приложения по команде из меню трея."""
        if hasattr(self, "tray") and self.tray:
            self.tray.hide()
        QApplication.quit()


# ─── Публичный класс JarvisUI (совместимость с main.py) ──────────────────────
class JarvisUI(MainWindow):
    """Обёртка для совместимости с main.py."""

    def __init__(self, face_path: str = "face.png"):
        if not QApplication.instance():
            self._app = QApplication(sys.argv)
        else:
            self._app = QApplication.instance()
        super().__init__(face_path)
        self.show()

    @property
    def root(self):
        """Псевдо-атрибут для совместимости — возвращает приложение."""
        return self._app

    def bring_to_front(self):
        """Разворачивает окно и выводит на передний план. Потокобезопасно."""
        self._front_sig.emit()

    def _bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_mute(self):
        """Переключает микрофон. Потокобезопасно: зовётся из потока хоткеев."""
        self._mute_sig.emit()

    def mainloop(self):
        sys.exit(self._app.exec())
