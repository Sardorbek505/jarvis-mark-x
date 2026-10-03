"""Окно без системной полосы Windows: края для размера, кнопки в шапке, двойной клик."""
import pytest
from PyQt6.QtCore import QPoint, QRect, Qt


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_edges_under_cursor():
    from ui_frame import cursor_for, edges_at
    r = QRect(0, 0, 800, 600)
    assert not edges_at(QPoint(400, 300), r)
    assert edges_at(QPoint(2, 300), r) == Qt.Edge.LeftEdge
    assert edges_at(QPoint(798, 598), r) == Qt.Edge.RightEdge | Qt.Edge.BottomEdge
    assert cursor_for(edges_at(QPoint(1, 1), r)) == Qt.CursorShape.SizeFDiagCursor
    assert cursor_for(edges_at(QPoint(799, 1), r)) == Qt.CursorShape.SizeBDiagCursor
    assert cursor_for(edges_at(QPoint(400, 599), r)) == Qt.CursorShape.SizeVerCursor
    assert cursor_for(Qt.Edge(0)) is None


def test_window_loses_system_title_bar_and_gets_caption_buttons(qapp):
    from PyQt6.QtWidgets import QWidget

    from ui_frame import Frameless, caption_buttons
    w = QWidget()
    Frameless(w, [])
    assert w.windowFlags() & Qt.WindowType.FramelessWindowHint
    closed = []
    w.close = lambda: closed.append(1)                     # не закрывать тестовое окно по-настоящему
    mini, maxi, close = caption_buttons(w, {"text": "#aaa", "white": "#fff", "hover": "#333"})
    assert [b.toolTip() for b in (mini, maxi, close)] == ["Свернуть", "Развернуть", "Закрыть Джарвиса"]
    w.show()
    assert w.contentsMargins().left() > 0                  # края — для изменения размера
    maxi.click()
    assert w.isMaximized()
    qapp.processEvents()
    assert w.contentsMargins().left() == 0                 # у развёрнутого окна краёв нет
    maxi.click()
    assert not w.isMaximized()
    close.click()
    assert closed == [1]
    w.hide()
    w.deleteLater()
    qapp.processEvents()
