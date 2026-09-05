"""Unit tests for ArcReactorWidget floating HUD."""

import sys
import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPointF


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["", "-platform", "offscreen"])
    return app


def test_arc_reactor_widget_init(qapp):
    from ui import ArcReactorWidget

    widget = ArcReactorWidget()
    assert widget.width() == 140
    assert widget.height() == 140
    assert widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True

    # Проверяем начальное состояние
    assert widget.state in ("ОЖИДАЕТ", "IDLE")
    assert widget.level == 0.0

    # Проверяем реакцию на изменение состояния
    widget.set_state("LISTENING")
    assert widget.state == "СЛУШАЕТ"

    widget.set_state("THINKING")
    assert widget.state == "ДУМАЕТ"

    widget.set_state("SPEAKING")
    assert widget.state == "ГОВОРИТ"

    widget.set_state("MUTED")
    assert widget.state == "ОТКЛЮЧЁН"

    # Проверяем отклик на громкость
    widget.set_level(0.8)
    assert widget.level == 0.8

    # Проверяем шаг анимации (60 FPS tick)
    initial_angle = widget._angle_coils
    widget._step()
    assert widget._angle_coils != initial_angle
    assert widget._tick == 1


def test_arc_reactor_widget_click_restore(qapp):
    from ui import ArcReactorWidget

    widget = ArcReactorWidget()
    restored = []
    widget.on_restore = lambda: restored.append(True)

    # Имитация клика мыши без перетаскивания
    class DummyEvent:
        def __init__(self, btn):
            self._btn = btn

        def button(self):
            return self._btn

        def accept(self):
            pass

    widget._dragged = False
    widget.mouseReleaseEvent(DummyEvent(Qt.MouseButton.LeftButton))
    assert restored == [True]


def test_mainwindow_compact_mode_toggle(qapp):
    from ui import MainWindow

    # Создаем окно с фиктивным путем к лицу
    win = MainWindow("face.png")
    assert win._compact_mode is False

    # Переход в компактный режим (виджет Arc Reactor)
    win.set_compact_mode(True)
    assert win._compact_mode is True
    assert win.isHidden() is True
    assert win._arc_reactor.isVisible() is True

    # Возврат в полный интерфейс
    win.set_compact_mode(False)
    assert win._compact_mode is False
    assert win.isVisible() is True
    assert win._arc_reactor.isHidden() is True

    win._arc_reactor.close()
    win.close()
