"""
ДЖАРВИС — Голосовой ИИ-ассистент
UI: точная копия Mark-XXXIX с русскоязычными надписями
Требования: pip install PyQt6 psutil Pillow
"""

from __future__ import annotations

import html
import math
import platform
import random
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
    QBrush, QColor, QFont, QKeySequence, QPainter, QPen, QPixmap,
    QShortcut, QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QSizePolicy, QSystemTrayIcon, QTextEdit,
    QVBoxLayout, QWidget,
)

import logging

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
class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Микрофон включён сразу: собственные динамики он больше не слушает
        # (см. speaker_meter.py), а раньше приходилось стартовать молча —
        # иначе Джарвис отвечал музыке. Выключить: Ctrl+M.
        self.muted    = False
        self.speaking = False
        self.state    = "ИНИЦИАЛИЗАЦИЯ"

        self._tick      = 0
        self._scale     = 1.0
        self._tgt_scale = 1.0
        self._halo      = 55.0
        self._tgt_halo  = 55.0
        self._last_t    = time.time()
        # Громкость 0..1: слева — микрофон, справа — собственный голос.
        # До этого HUD «реагировал» на random.uniform, то есть дышал ровно так
        # же в тишине и на крике. Живое число берётся быстро (атака), а спадает
        # плавно (затухание) — так ведёт себя стрелочный индикатор уровня, и
        # именно поэтому она выглядит связанной со звуком, а не сама по себе.
        self.level      = 0.0
        # Инструмент, на который сейчас «наведён» прицел, и когда он погаснет.
        self._tool: str | None = None
        self._tool_until = 0.0
        self._tool_lock  = 0.0        # 0..1, насколько скобки сомкнулись
        # Бегущая история громкости — та самая полоска-осциллограф внизу.
        # 36 столбиков на 60 fps = окно около 0.6 секунды: достаточно, чтобы
        # глаз увидел ритм фразы, и мало, чтобы она не превратилась в кашу.
        self._wave: list[float] = [0.0] * 36
        self._scan      = 0.0
        self._scan2     = 180.0
        self._rings     = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink     = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None

        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)  # ~60 fps

    def _load_face(self, path: str):
        try:
            import io
            from PIL import Image, ImageDraw
            from core.paths import get_base_dir, get_app_dir

            p = Path(path)
            if not p.is_absolute() or not p.exists():
                candidates = [
                    get_base_dir() / path,
                    get_app_dir() / path,
                    Path(__file__).resolve().parent / path,
                    get_base_dir() / "face.png",
                    get_app_dir() / "face.png",
                    Path(path),
                ]
                for c in candidates:
                    if c.exists():
                        p = c
                        break

            if not p.exists():
                self._face_px = None
                return

            img = Image.open(str(p)).convert("RGBA")
            sz = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception as exc:
            _logger.debug("Face image loading error: %s", exc)
            self._face_px = None

    def feed_level(self, value: float):
        """Живая громкость 0..1. Атака мгновенная, спад — в _step."""
        self.level = max(self.level, max(0.0, min(1.0, value)))

    def lock_on(self, tool: str, seconds: float = 2.6):
        """Прицел на инструмент: скобки смыкаются и подписываются именем."""
        self._tool = tool
        self._tool_until = time.time() + seconds
        self._tool_lock = 0.0

    def _step(self):
        self._tick += 1
        now = time.time()

        # Спад громкости. Быстрее, когда говорит Джарвис: его речь рвётся
        # паузами между словами, и медленный спад смазал бы их в одно гудение.
        self.level *= 0.88 if self.speaking else 0.82

        # Цели считаются из громкости каждый кадр, а не выдумываются раз в
        # полсекунды. Дыхание в тишине оставлено намеренно: мёртвый HUD
        # выглядит выключенным, а не спокойным.
        breath = 0.004 * math.sin(self._tick * 0.03)
        if self.muted:
            self._tgt_scale = 1.0 + breath * 0.3
            self._tgt_halo  = 18.0
        elif self.speaking:
            self._tgt_scale = 1.0 + breath + self.level * 0.16
            self._tgt_halo  = 110.0 + self.level * 95.0
        else:
            self._tgt_scale = 1.0 + breath + self.level * 0.05
            self._tgt_halo  = 46.0 + self.level * 70.0
        self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        if self._tool and now > self._tool_until:
            self._tool = None
        if self._tool:
            # Ease-out: скобки быстро идут к цели и мягко встают на место.
            self._tool_lock += (1.0 - self._tool_lock) * 0.22
        else:
            self._tool_lock *= 0.85

        speeds = [1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360
        self._scan  = (self._scan  + (3.0 if self.speaking else 1.3)) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75)) % 360

        fw = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.07 if self.speaking else 0.025):
            self._pulses.append(0.0)

        # Искры летят тем гуще, чем громче голос — на тихой фразе их почти нет.
        if self.speaking and random.random() < 0.06 + self.level * 0.45:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._wave.pop(0)
        self._wave.append(0.0 if self.muted else self.level)

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0

        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # Точечная сетка
        p.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)

        r_face = fw * 0.31
        pri_col = C.MUTED_C if self.muted else C.PRI

        # Ореол (halo glow)
        for i in range(10):
            r = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a = max(0, min(255, int(self._halo * 0.085 * frc)))
            p.setPen(QPen(qcol(pri_col, a), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # Пульсирующие кольца
        for pr in self._pulses:
            a = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            p.setPen(QPen(qcol(pri_col, a), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # Вращающиеся дуги
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base = self._rings[idx]
            a_val = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            p.setPen(QPen(qcol(pri_col, a_val), w_r))
            p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # Сканеры
        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(pri_col, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # Деления
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        # Прицельная сетка
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # Угловые скобки
        bl = 24
        bc = qcol(C.PRI, 210)
        hl = cx - fw // 2
        hr = cx + fw // 2
        ht = cy - fw // 2
        hb = cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # Лицо / орбита
        if self._face_px:
            fsz = int(fw * 0.62 * self._scale)
            scaled = self._face_px.scaled(
                fsz, fsz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(int(cx - fsz / 2), int(cy - fsz / 2), scaled)
        else:
            orb_r = int(fw * 0.27 * self._scale)
            oc = (200, 0, 50) if self.muted else (0, 60, 110)
            for i in range(8, 0, -1):
                r2 = int(orb_r * i / 8)
                frc = i / 8
                a = max(0, min(255, int(self._halo * 1.1 * frc)))
                p.setBrush(QBrush(QColor(int(oc[0]*frc), int(oc[1]*frc), int(oc[2]*frc), a)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))
            p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2))), 1))
            p.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
            p.drawText(QRectF(cx - 80, cy - 14, 160, 28),
                       Qt.AlignmentFlag.AlignCenter, "Д.Ж.А.Р.В.И.С")

        # Частицы
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # Прицел на инструмент. Джарвис у Старка никогда не работает молча:
        # он всегда показывает, на что именно наведён. Скобки приходят
        # снаружи внутрь (ease-out) и подписываются именем модуля.
        if self._tool_lock > 0.01:
            k = self._tool_lock
            half = fw * (0.42 - 0.10 * k)          # смыкаются к центру
            arm  = fw * 0.055
            a    = int(230 * min(1.0, k * 1.4))
            p.setPen(QPen(qcol(C.ACC2, a), 2))
            for sx in (-1, 1):
                for sy_ in (-1, 1):
                    x = cx + sx * half
                    y = cy + sy_ * half * 0.62
                    p.drawLine(QPointF(x, y), QPointF(x - sx * arm, y))
                    p.drawLine(QPointF(x, y), QPointF(x, y - sy_ * arm))
            if self._tool:
                p.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
                p.setPen(QPen(qcol(C.ACC2, a), 1))
                p.drawText(
                    QRectF(0, cy - half * 0.62 - 26, W, 20),
                    Qt.AlignmentFlag.AlignCenter,
                    f"▏ {self._tool.upper().replace('_', ' ')} ▕",
                )

        # Статус
        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "⊘ ОТКЛЮЧЁН", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "● ГОВОРИТ", qcol(C.ACC)
        elif self.state == "ДУМАЕТ":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym} ДУМАЕТ", qcol(C.ACC2)
        elif self.state == "ОБРАБОТКА":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym} ОБРАБОТКА", qcol(C.ACC2)
        elif self.state == "СЛУШАЕТ":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym} СЛУШАЕТ", qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym} {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # Волновая форма
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        for i in range(N):
            # Столбики — это записанная громкость, а не случайные числа:
            # полоска бежит в такт голосу и замирает, когда никто не говорит.
            lvl = self._wave[i] if i < len(self._wave) else 0.0
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            elif lvl > 0.02:
                hgt = int(3 + lvl * 22)
                cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
            else:
                # Тишина — ровная линия с едва заметной рябью, чтобы полоска
                # читалась как живая, а не как погасшая.
                hgt = int(3 + 1.5 * math.sin(self._tick * 0.09 + i * 0.6))
                cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)


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

    # ── Публичный API ──────────────────────────────────────────────────────────
    def write_log(self, text: str):
        self._log_sig.emit(text)

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
        keys = load_api_keys()
        api_key = keys.get("gemini_api_key", "").strip()
        if api_key:
            print("[UI] API ключ найден, пропускаем инициализацию...")
            return None

        # Ключа нет или файл повреждён — показываем оверлей
        reason = "init"
        self._key_ready = threading.Event()
        self._setup_reason = reason
        QTimer.singleShot(0, lambda: self._show_overlay(reason))
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

    def mainloop(self):
        sys.exit(self._app.exec())
