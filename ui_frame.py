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
from PyQt6.QtWidgets import QAbstractButton, QApplication, QPushButton, QWidget

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

    drag_area(widget, global_pos) -> bool решает, можно ли тащить окно за
    это место (пустое место шапки — да, кнопки — нет)."""

    def __init__(self, window: QWidget, drag_area):
        super().__init__(window)
        self.window = window
        self.drag_area = drag_area
        self._cursor_set = False
        window.setWindowFlags(window.windowFlags() | Qt.WindowType.FramelessWindowHint
                              | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowSystemMenuHint)
        QApplication.instance().installEventFilter(self)

    def _ours(self, obj) -> bool:
        return isinstance(obj, QWidget) and obj.window() is self.window

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                     QEvent.Type.HoverMove) or not self._ours(obj):
            return False
        w = self.window
        gpos = ev.globalPosition().toPoint() if hasattr(ev, "globalPosition") else None
        if gpos is None:
            return False
        local = w.mapFromGlobal(gpos)
        edges = Qt.Edge(0) if w.isMaximized() else edges_at(local, w.rect())
        if t in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
            cur = cursor_for(edges)
            if cur is not None:
                w.setCursor(cur)
                self._cursor_set = True
            elif self._cursor_set:
                w.unsetCursor()
                self._cursor_set = False
            return False
        if ev.button() != Qt.MouseButton.LeftButton:
            return False
        handle = w.windowHandle()
        if t == QEvent.Type.MouseButtonPress and edges and handle is not None:
            handle.startSystemResize(edges)
            return True
        if isinstance(obj, QAbstractButton) or not self.drag_area(obj, gpos):
            return False
        if t == QEvent.Type.MouseButtonDblClick:
            w.showNormal() if w.isMaximized() else w.showMaximized()
            return True
        if handle is not None:
            handle.startSystemMove()
            return True
        return False
