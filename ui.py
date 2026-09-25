"""
ДЖАРВИС — Голосовой ИИ-ассистент
UI: точная копия Mark-XXXIX с русскоязычными надписями
Требования: pip install PyQt6 psutil Pillow
"""

from __future__ import annotations

import html
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


# ─── Цветовая палитра (идентично оригиналу) ───────────────────────────────────
class C:
    BG       = "#00060a"
    PANEL    = "#010d14"
    PANEL2   = "#010f18"
    BORDER   = "#0d3347"
    BORDER_B = "#1a5c7a"
    BORDER_A = "#0f4060"
    PRI      = "#00d4ff"
    PRI_DIM  = "#007a99"
    PRI_GHO  = "#001f2e"
    ACC      = "#ff6b00"
    ACC2     = "#ffcc00"
    GREEN    = "#00ff88"
    GREEN_D  = "#00aa55"
    RED      = "#ff3355"
    MUTED_C  = "#ff3366"
    TEXT     = "#8ffcff"
    TEXT_DIM = "#3a8a9a"
    TEXT_MED = "#5ab8cc"
    WHITE    = "#d8f8ff"
    DARK     = "#000d14"
    BAR_BG   = "#011520"


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
    "ИНИЦИАЛИЗАЦИЯ":    (255, 70, 96),     # красный — запуск / нет связи
    "ПЕРЕПОДКЛЮЧЕНИЕ":  (255, 70, 96),
    "ОТКЛЮЧЁН":         (105, 112, 124),   # серый — микрофон выключен
}
_SUB_HOLD_SEC = 6.0      # сколько субтитр висит после последнего слова


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

        self._orb = DotOrb()
        self._rgb = list(_STATE_RGB["ИНИЦИАЛИЗАЦИЯ"])
        self._ring = 0.0                      # поворот внешнего кольца, градусы
        self._clock = 0.0                     # время анимации, с
        self._last = time.monotonic()

        self._tool: str | None = None
        self._tool_until = 0.0
        self._tool_lock = 0.0                 # 0..1, проявление подписи инструмента

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
        p.fillRect(self.rect(), QColor(2, 5, 8))

        e = self._orb.energy
        fw = min(W, H)
        R = fw * 0.25 * (1.0 + 0.05 * e)
        cx, cy = W / 2, H / 2 - fw * 0.03

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
            p.drawText(QRectF(0, pill.bottom() + 6, W, 18), Qt.AlignmentFlag.AlignCenter,
                       "▸ " + self._tool.replace("_", " ").upper())

        # Субтитры: то, что Джарвис говорит сейчас. Длинный ответ — хвост.
        if self._sub_alpha > 0.02 and self._sub_text:
            text = self._sub_text
            if len(text) > 150:
                text = "…" + text[-150:].split(" ", 1)[-1]
            p.setFont(QFont("Segoe UI", max(10, int(fw / 52))))
            bw = min(W * 0.84, 640)
            top = cy + ring_r + fw * 0.05
            p.setPen(QPen(QColor(236, 242, 246, int(235 * self._sub_alpha)), 1))
            p.drawText(QRectF(cx - bw / 2, top, bw, H - top - 8),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                       | Qt.TextFlag.TextWordWrap, text)


# ─── Виджет метрики ───────────────────────────────────────────────────────────
class MetricBar(QWidget):
    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h = 4
        bar_y = H - bar_h - 5
        bar_w = W - 12
        bar_x = 6
        fill_w = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        bar_col = (qcol(C.RED) if self._value > 85
                   else qcol(C.ACC) if self._value > 65
                   else qcol(self._color))
        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self._label)
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self._text)


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
                background-color: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 6px;
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 3px;
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

    def _handle_append(self, text: str):
        if not text:
            return

        tl = text.strip().lower()
        now_str = time.strftime("%H:%M")

        if tl.startswith("вы:") or tl.startswith("you:"):
            content = text.split(":", 1)[1].strip()
            safe_content = html.escape(content)
            card = (
                f'<div style="margin: 4px 0px 6px 0px; padding: 6px 8px; background: rgba(0, 32, 48, 0.6); '
                f'border-left: 3px solid #00d4ff; border-radius: 4px;">'
                f'<table width="100%" style="margin-bottom: 2px;"><tr>'
                f'<td style="font-family: \'Segoe UI\', sans-serif; font-size: 10px; font-weight: bold; color: #50c8e8; letter-spacing: 1px;">ВЫ</td>'
                f'<td align="right" style="font-family: monospace; font-size: 9px; color: #3a7588;">{now_str}</td>'
                f'</tr></table>'
                f'<div style="font-family: \'Segoe UI\', sans-serif; font-size: 12px; color: #ffffff; line-height: 135%;">{safe_content}</div>'
                f'</div>'
            )
        elif tl.startswith("джарвис:") or tl.startswith("jarvis:"):
            content = text.split(":", 1)[1].strip()
            safe_content = html.escape(content)
            card = (
                f'<div style="margin: 4px 0px 6px 0px; padding: 6px 8px; background: rgba(0, 48, 36, 0.6); '
                f'border-left: 3px solid #00ffaa; border-radius: 4px;">'
                f'<table width="100%" style="margin-bottom: 2px;"><tr>'
                f'<td style="font-family: \'Segoe UI\', sans-serif; font-size: 10px; font-weight: bold; color: #00ffaa; letter-spacing: 1px;">◈ ДЖАРВИС</td>'
                f'<td align="right" style="font-family: monospace; font-size: 9px; color: #2a7a5c;">{now_str}</td>'
                f'</tr></table>'
                f'<div style="font-family: \'Segoe UI\', sans-serif; font-size: 12px; color: #dcf8ff; line-height: 135%;">{safe_content}</div>'
                f'</div>'
            )
        elif tl.startswith("err:") or "ошибка" in tl:
            content = text.split(":", 1)[1].strip() if ":" in text else text
            safe_content = html.escape(content)
            card = (
                f'<div style="margin: 3px 0px; padding: 4px 6px; background: rgba(60, 10, 20, 0.45); '
                f'border-left: 2px solid #ff3b5c; border-radius: 3px;">'
                f'<span style="font-family: monospace; font-size: 9px; font-weight: bold; color: #ff3b5c;">ERR:</span> '
                f'<span style="font-family: \'Segoe UI\', sans-serif; font-size: 11px; color: #ff99aa;">{safe_content}</span>'
                f'</div>'
            )
        elif tl.startswith("sys:"):
            content = text.split(":", 1)[1].strip()
            safe_content = html.escape(content)
            card = (
                f'<div style="margin: 2px 0px; padding: 3px 6px; background: rgba(30, 25, 10, 0.35); '
                f'border-left: 2px solid #d49b35; border-radius: 3px;">'
                f'<span style="font-family: monospace; font-size: 9px; font-weight: bold; color: #d49b35;">SYS:</span> '
                f'<span style="font-family: \'Segoe UI\', sans-serif; font-size: 10px; color: #8ab0b8;">{safe_content}</span>'
                f'</div>'
            )
        else:
            safe_content = html.escape(text)
            card = (
                f'<div style="margin: 2px 0px; font-family: \'Segoe UI\', sans-serif; font-size: 11px; color: {C.TEXT_DIM};">'
                f'{safe_content}</div>'
            )

        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cur)
        self.insertHtml(card)
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
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Левая панель ────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(148)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        def _sec_label(txt: str) -> QLabel:
            w = QLabel(txt)
            w.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            w.setStyleSheet(f"color: {C.TEXT_DIM}; letter-spacing: 2px;")
            return w

        left_lay.addWidget(_sec_label("◈ СИСТЕМА"))

        self._cpu_bar = MetricBar("ЦПУ",  C.PRI)
        self._mem_bar = MetricBar("ОЗУ",  C.ACC2)
        self._net_bar = MetricBar("СЕТЬ", C.GREEN)
        for bar in (self._cpu_bar, self._mem_bar, self._net_bar):
            left_lay.addWidget(bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        left_lay.addWidget(sep)
        left_lay.addWidget(_sec_label("◈ СТАТУС"))

        self._status_lbl = QLabel("ОЖИДАНИЕ")
        self._status_lbl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._status_lbl.setStyleSheet(f"color: {C.ACC2};")
        self._status_lbl.setWordWrap(True)
        left_lay.addWidget(self._status_lbl)

        left_lay.addStretch()

        # Кнопка: Тихий режим
        self._mute_btn = QPushButton("🔇  ТИХИЙ")
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        left_lay.addWidget(self._mute_btn)

        root.addWidget(left)

        # ── Центральный HUD ─────────────────────────────────────────
        self._hud = HudCanvas(face_path)
        root.addWidget(self._hud, stretch=1)

        # ── Правая панель ───────────────────────────────────────────
        right = QWidget()
        right.setFixedWidth(340)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)

        right_lay.addWidget(_sec_label("◈ ДИАЛОГ"))

        self._log = LogWidget()
        right_lay.addWidget(self._log, stretch=1)

        right_lay.addWidget(_sec_label("◈ ТЕКСТОВЫЙ ВВОД"))

        input_row = QHBoxLayout()
        input_row.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Напишите команду... (Enter)")
        self._input.setFont(QFont("Segoe UI", 9))
        self._input.setFixedHeight(32)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; background: #00141e; }}
        """)
        self._input.returnPressed.connect(self._send_text)
        input_row.addWidget(self._input)

        send_btn = QPushButton("▸")
        send_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        send_btn.setFixedSize(32, 32)
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.clicked.connect(self._send_text)
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 4px;
            }}
            QPushButton:hover {{ background: {C.BORDER_A}; border: 1px solid {C.PRI}; }}
        """)
        input_row.addWidget(send_btn)

        right_lay.addLayout(input_row)

        # Кнопки внизу
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        for label, slot in [("ОЧИСТИТЬ", self._clear_log), ("ВЫХОД", self.close)]:
            b = QPushButton(label)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setFixedHeight(26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        right_lay.addLayout(btn_row)

        root.addWidget(right)

        # ── Оверлей настройки (поверх всего) ───────────────────────
        self._overlay = None
        self._setup_done = False

        # ── Горячие клавиши ─────────────────────────────────────────
        QShortcut(QKeySequence("Ctrl+M"), self).activated.connect(self._toggle_mute)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self._clear_log)

        # ── Таймеры ─────────────────────────────────────────────────
        self._metric_timer = QTimer(self)
        self._metric_timer.timeout.connect(self._update_metrics)
        self._metric_timer.start(2000)

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._level_sig.connect(self._hud.feed_level)
        self._tool_sig.connect(self._hud.lock_on)
        self._sub_sig.connect(self._hud.set_subtitle)
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

    def set_state(self, state: str):
        self._state_sig.emit(state)

    def set_level(self, value: float):
        """Громкость 0..1 — ею дышит весь HUD. Зовётся из аудио-потока."""
        self._level_sig.emit(float(value))

    def lock_on(self, tool: str):
        """Навести прицел на инструмент, который сейчас выполняется."""
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
        self._status_lbl.setText(ru)

        color = {
            "ОЖИДАЕТ":     C.TEXT_DIM,
            "СЛУШАЕТ":      C.GREEN,
            "ДУМАЕТ":       C.ACC2,
            "ГОВОРИТ":      C.ACC,
            "ОБРАБОТКА":    C.ACC2,
            "ИНИЦИАЛИЗАЦИЯ": C.PRI,
        }.get(ru, C.TEXT_DIM)
        self._status_lbl.setStyleSheet(f"color: {color};")

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

    def _style_mute_btn(self):
        if self.muted:
            self._mute_btn.setText("🔊  ВКЛЮЧИТЬ")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.MUTED_C}; color: #000;
                    border: none; border-radius: 3px; font-weight: bold;
                }}
                QPushButton:hover {{ background: #ff6688; }}
            """)
        else:
            self._mute_btn.setText("🔇  ТИХИЙ")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.MUTED_C}; border: 1px solid {C.MUTED_C}; }}
            """)

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
        self._cpu_bar.set_value(snap["cpu"], f"{snap['cpu']:.0f}%")
        self._mem_bar.set_value(snap["mem"], f"{snap['mem']:.0f}%")
        net = snap["net"]
        net_str = f"{net:.1f} МБ/с" if net >= 0.1 else f"{net*1024:.0f} КБ/с"
        self._net_bar.set_value(min(100, net * 10), net_str)

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
