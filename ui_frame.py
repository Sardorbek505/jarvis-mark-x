"""Окно без системной полосы заголовка Windows — шапка Джарвиса вместо неё.

Системная серая полоса «Д.Ж.А.Р.В.И.С — Голосовой ИИ» с кнопками Windows
выбивалась из тёмного интерфейса. Окно теперь без рамки, а то, что она
давала, делает шапка приложения:
  • тащить окно — за пустое место шапки (через startSystemMove, поэтому
    работает прилипание к краям экрана и Win+стрелки);
  • двойной клик по шапке — развернуть / вернуть;
  • менять размер — за края и углы окна (startSystemResize);
  • кнопки «—  ▢  ✕» справа в шапке.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QAbstractButton, QPushButton, QWidget

BORDER = 6            # ширина невидимой «рамки» для изменения размера, px

_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
}


def edges_at(pos: QPoint, rect: QRect, border: int = BORDER) -> Qt.Edge:
    """Какие края окна под курсором (pos — в координатах окна). Пусто — середина."""
    e = Qt.Edge(0)
    if pos.x() <= border:
        e |= Qt.Edge.LeftEdge
    elif pos.x() >= rect.width() - border:
        e |= Qt.Edge.RightEdge
    if pos.y() <= border:
        e |= Qt.Edge.TopEdge
    elif pos.y() >= rect.height() - border:
        e |= Qt.Edge.BottomEdge
    return e


def cursor_for(e: Qt.Edge) -> Qt.CursorShape | None:
    if not e:
        return None
    diag_main = (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
    diag_anti = (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.Edge.LeftEdge | Qt.Edge.BottomEdge)
    if e in diag_main:
        return Qt.CursorShape.SizeFDiagCursor
    if e in diag_anti:
        return Qt.CursorShape.SizeBDiagCursor
    for edge, cur in _CURSORS.items():
        if e & edge:
            return cur
    return None


def caption_buttons(window: QWidget, colors: dict) -> list[QPushButton]:
    """Кнопки «свернуть / развернуть / закрыть» для шапки."""
    def make(text: str, tip: str, hover_bg: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("caption")
        b.setToolTip(tip)
        b.setFixedSize(40, 28)
        b.setFont(QFont("Segoe UI Symbol", 10))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {colors['text']}; border: none; border-radius: 6px; }}
            QPushButton:hover {{ background: {hover_bg}; color: {colors['white']}; }}
        """)
        b.clicked.connect(slot)
        return b

    def toggle_max():
        window.showNormal() if window.isMaximized() else window.showMaximized()

    return [
        make("—", "Свернуть", colors["hover"], window.showMinimized),
        make("▢", "Развернуть", colors["hover"], toggle_max),
        make("✕", "Закрыть Джарвиса", "#c42b1c", window.close),
    ]


class Frameless(QObject):
    """Убирает рамку Windows у окна и даёт шапке и краям её работу.

    Фильтр событий — только на самом окне и на виджетах шапки, не на всём
    приложении: глобальный фильтр Qt вызывал у окна, которое уже удаляется,
    и Python падал при выходе. Чтобы края окна получали мышь сами, вокруг
    содержимого — невидимая полоска BORDER (у развёрнутого окна её нет)."""

    def __init__(self, window: QWidget, drag_widgets: list[QWidget]):
        super().__init__(window)
        self.window = window
        self.drag = list(drag_widgets)
        self._cursor_set = False
        window.setWindowFlags(window.windowFlags() | Qt.WindowType.FramelessWindowHint
                              | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowSystemMenuHint)
        window.setMouseTracking(True)
        window.installEventFilter(self)
        for w in self.drag:
            w.installEventFilter(self)
        self._margins()

    def _margins(self):
        b = 0 if self.window.isMaximized() else BORDER
        self.window.setContentsMargins(b, b, b, b)

    def eventFilter(self, obj, ev):
        t = ev.type()
        w = self.window
        if obj is w:
            if t == QEvent.Type.WindowStateChange:
                self._margins()
                return False
            if t not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
                return False
            edges = Qt.Edge(0) if w.isMaximized() else edges_at(ev.position().toPoint(), w.rect())
            if t == QEvent.Type.MouseMove:
                cur = cursor_for(edges)
                if cur is not None:
                    w.setCursor(cur)
                    self._cursor_set = True
                elif self._cursor_set:
                    w.unsetCursor()
                    self._cursor_set = False
                return False
            if edges and ev.button() == Qt.MouseButton.LeftButton and w.windowHandle() is not None:
                w.windowHandle().startSystemResize(edges)
                return True
            return False
        if obj in self.drag and not isinstance(obj, QAbstractButton):
            if t not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                return False
            if ev.button() != Qt.MouseButton.LeftButton:
                return False
            if t == QEvent.Type.MouseButtonDblClick:
                w.showNormal() if w.isMaximized() else w.showMaximized()
                return True
            if w.windowHandle() is not None:
                w.windowHandle().startSystemMove()
                return True
        return False
