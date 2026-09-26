"""Панель браузера рядом с шаром (Qt). Картинку и ввод даёт core/browser_panel.py.

Сверху — строка: назад, вперёд, обновить, адрес, «на весь экран», закрыть.
Ниже — страница. Картинка вписывается в панель с сохранением пропорций,
поэтому координаты клика пересчитываются из точек панели в точки страницы.
Всё, что говорит с Chrome, идёт в отдельном потоке: окно не подвисает,
даже если страница думает.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

BG, PANEL, LINE, LINE2 = "#030609", "#070c11", "#151e27", "#243240"
PRI, TEXT, DIM = "#3fd0bd", "#d6dee5", "#5c6873"


class PageView(QWidget):
    """Картинка страницы и перевод мыши/клавиатуры в команды странице."""

    def __init__(self, owner: "BrowserPanelView"):
        super().__init__(owner)
        self.owner = owner
        self.img: QImage | None = None
        self.draw_rect = QRectF()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._last_move = 0.0

    # координаты: точка панели → точка страницы
    def to_page(self, pos: QPointF) -> tuple[float, float] | None:
        r = self.draw_rect
        if r.isEmpty() or not r.contains(pos):
            return None
        pw, ph = self.owner.page_size()
        return ((pos.x() - r.x()) * pw / r.width(), (pos.y() - r.y()) * ph / r.height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0a1017"))
        if self.img is None or self.img.isNull():
            p.setPen(QPen(QColor(DIM)))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Загружаю страницу…")
            self.draw_rect = QRectF()
            return
        iw, ih = self.img.width(), self.img.height()
        k = min(self.width() / iw, self.height() / ih)
        w, h = iw * k, ih * k
        self.draw_rect = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self.draw_rect, self.img)

    def _mods(self, ev) -> int:
        m = ev.modifiers()
        return ((1 if m & Qt.KeyboardModifier.AltModifier else 0)
                | (2 if m & Qt.KeyboardModifier.ControlModifier else 0)
                | (8 if m & Qt.KeyboardModifier.ShiftModifier else 0))

    # Всё из события читаем здесь, в потоке Qt: к моменту, когда фоновый
    # поток дойдёт до команды, Qt событие уже удалит (клики терялись).
    def _button(self, ev) -> str:
        return {Qt.MouseButton.RightButton: "right", Qt.MouseButton.MiddleButton: "middle"}.get(ev.button(), "left")

    def mousePressEvent(self, ev):
        self.setFocus()
        pt = self.to_page(ev.position())
        if pt:
            btn, mods = self._button(ev), self._mods(ev)
            self.owner.do(lambda p: p.mouse("press", *pt, button=btn, mods=mods))

    def mouseDoubleClickEvent(self, ev):
        pt = self.to_page(ev.position())
        if pt:
            self.owner.do(lambda p: (p.mouse("press", *pt, clicks=2), p.mouse("release", *pt, clicks=2)))

    def mouseReleaseEvent(self, ev):
        pt = self.to_page(ev.position())
        if pt:
            btn, mods = self._button(ev), self._mods(ev)
            self.owner.do(lambda p: p.mouse("release", *pt, button=btn, mods=mods))

    def mouseMoveEvent(self, ev):
        now = time.monotonic()
        if now - self._last_move < 1 / 30:            # 30 раз в секунду хватает с запасом
            return
        self._last_move = now
        pt = self.to_page(ev.position())
        if pt:
            self.owner.do(lambda p: p.mouse("move", *pt))

    def wheelEvent(self, ev):
        pt = self.to_page(ev.position())
        if pt:
            d = ev.angleDelta()
            # Qt: вверх — плюс; страница: вниз — плюс. 120 единиц ≈ 100 пикселей.
            dx, dy = -d.x() * 100 / 120, -d.y() * 100 / 120
            self.owner.do(lambda p: p.wheel(*pt, dx, dy))

    _SPECIAL = {
        Qt.Key.Key_Return: "Enter", Qt.Key.Key_Enter: "Enter", Qt.Key.Key_Backspace: "Backspace",
        Qt.Key.Key_Tab: "Tab", Qt.Key.Key_Escape: "Escape", Qt.Key.Key_Delete: "Delete",
        Qt.Key.Key_Left: "ArrowLeft", Qt.Key.Key_Right: "ArrowRight", Qt.Key.Key_Up: "ArrowUp",
        Qt.Key.Key_Down: "ArrowDown", Qt.Key.Key_Home: "Home", Qt.Key.Key_End: "End",
        Qt.Key.Key_PageUp: "PageUp", Qt.Key.Key_PageDown: "PageDown",
    }

    def keyPressEvent(self, ev):
        mods = self._mods(ev)
        name = self._SPECIAL.get(ev.key())
        if name:
            self.owner.do(lambda p: p.key(name, mods))
        elif mods & 2 and Qt.Key.Key_A <= ev.key() <= Qt.Key.Key_Z:     # Ctrl+C/V/A/Z…
            ch = chr(ev.key()).lower()
            self.owner.do(lambda p: p.key(ch, mods))
        elif ev.text() and ev.text().isprintable():
            txt = ev.text()
            self.owner.do(lambda p: p.text(txt))
        else:
            super().keyPressEvent(ev)

    def focusNextPrevChild(self, _next):
        return False                                   # Tab — в страницу, а не по кнопкам


class BrowserPanelView(QFrame):
    """Панель целиком. panel — core.browser_panel.Panel (в тестах — подмена)."""

    frame_sig = pyqtSignal(bytes)
    url_sig = pyqtSignal(str)
    closed_sig = pyqtSignal()
    title_sig = pyqtSignal(str)

    def __init__(self, parent=None, panel=None):
        super().__init__(parent)
        if panel is None:
            from core import browser_panel
            panel = browser_panel.panel()
        self.panel = panel
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="panel-io")
        self.setObjectName("browserPanel")
        self.setStyleSheet(f"""
            #browserPanel {{ background: {PANEL}; border: 1px solid {LINE2}; border-radius: 14px; }}
            QPushButton {{ background: transparent; color: {PRI}; border: none; border-radius: 13px;
                           min-width: 26px; min-height: 26px; font: 13px 'Segoe UI'; }}
            QPushButton:hover {{ background: rgba(63,208,189,.12); }}
            QPushButton#close:hover {{ background: rgba(255,70,96,.15); color: #ff4660; }}
            QLineEdit {{ background: {BG}; color: {TEXT}; border: 1px solid {LINE}; border-radius: 13px;
                         padding: 3px 12px; font: 12px 'Segoe UI'; selection-background-color: {PRI}; }}
            QLineEdit:focus {{ border-color: {PRI}; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(4)

        from ui_icons import qicon

        def btn(icon, tip, fn, name=""):
            b = QPushButton()
            b.setIcon(qicon(icon, 14, PRI))
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if name:
                b.setObjectName(name)
            b.clicked.connect(fn)
            bar.addWidget(b)
            return b

        btn("back", "Назад", lambda: self.do(lambda p: p.back()))
        btn("next", "Вперёд", lambda: self.do(lambda p: p.forward()))
        btn("reload", "Обновить", lambda: self.do(lambda p: p.reload()))
        self.address = QLineEdit()
        self.address.setPlaceholderText("Адрес или поиск")
        self.address.returnPressed.connect(self._go)
        bar.addWidget(self.address, stretch=1)
        btn("expand", "На весь экран — в настоящем окне Chrome", self.expand)
        btn("close", "Закрыть панель", self.close_panel, name="close")
        lay.addLayout(bar)
        self.view = PageView(self)
        lay.addWidget(self.view, stretch=1)

        self.frame_sig.connect(self._on_frame)
        self.url_sig.connect(self._on_url)
        self.closed_sig.connect(self._on_closed)
        self.title_sig.connect(lambda t: self.setToolTip(t))
        panel.on_frame = lambda jpeg, meta: self.frame_sig.emit(jpeg)
        panel.on_url = lambda url: self.url_sig.emit(url)
        panel.on_closed = lambda: self.closed_sig.emit()

        # Размер страницы — по размеру панели, но не на каждый пиксель ресайза.
        self._resize_tmr = QTimer(self)
        self._resize_tmr.setSingleShot(True)
        self._resize_tmr.timeout.connect(self._sync_size)
        self.on_visibility = lambda shown: None       # MainWindow: сдвинуть шар
        self.hide()

    # ── связь с Chrome (в своём потоке) ──────────────────────────────────────
    def do(self, fn):
        def run():
            try:
                fn(self.panel)
            except Exception as exc:
                logger.debug("Панель браузера: %s", exc)
        self._pool.submit(run)

    def page_size(self) -> tuple[float, float]:
        w, h, _ = getattr(self.panel, "_size", (self.view.width(), self.view.height(), 1.0))
        return float(w), float(h)

    def view_size(self) -> tuple[int, int, float]:
        dpr = self.devicePixelRatioF() if self.isVisible() else 1.0
        return max(320, self.view.width()), max(240, self.view.height()), float(dpr)

    # ── события ───────────────────────────────────────────────────────────────
    def _on_frame(self, jpeg: bytes):
        img = QImage.fromData(jpeg, "JPG")
        if not img.isNull():
            self.view.img = img
            self.view.update()

    def _on_url(self, url: str):
        if not self.address.hasFocus():
            self.address.setText("" if url.startswith(("about:", "chrome-error:")) else url)
            self.address.setCursorPosition(0)

    def _on_closed(self):
        if self.isVisible():
            self.hide()
            self.on_visibility(False)

    def _go(self):
        text = self.address.text().strip()
        if text:
            self.view.setFocus()
            self.do(lambda p: p.go(text))

    def _sync_size(self):
        w, h, d = self.view_size()
        self.do(lambda p: p.resize(w, h, d))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.isVisible():
            self._resize_tmr.start(250)

    # ── наружу ────────────────────────────────────────────────────────────────
    def reveal(self, url: str = ""):
        """Показать панель (трансляция уже запущена browser_control)."""
        if url:
            self._on_url(url)
        was = self.isVisible()
        self.show()
        self.raise_()
        if not was:
            self.on_visibility(True)
        self._resize_tmr.start(60)

    def expand(self):
        self.hide()
        self.on_visibility(False)
        self.do(lambda p: p.expand())

    def close_panel(self):
        self.hide()
        self.on_visibility(False)
        self.do(lambda p: p.close())
