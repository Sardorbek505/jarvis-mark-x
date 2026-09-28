"""Анимации окна (ui_anim): красиво — и не тормозят.

Проверяем, что каждая анимация доходит до конца и УБИРАЕТ за собой (ни
оверлеев, ни таймеров не остаётся крутиться впустую), и что кадр перехода
между экранами рисуется быстро (снимки, а не эффекты на живых виджетах)."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, QVariantAnimation  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QStackedWidget, QWidget  # noqa: E402

app = QApplication.instance() or QApplication([])

import ui_anim as A  # noqa: E402


def _spin(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def _live_anims(root):
    return [a for a in root.findChildren(QVariantAnimation)
            if a.state() == QVariantAnimation.State.Running]


def test_page_transition_is_fast_and_cleans_up():
    stack = QStackedWidget()
    stack.resize(1100, 740)
    a, b = QWidget(), QWidget()
    a.setStyleSheet("background: #102030")
    b.setStyleSheet("background: #304050")
    stack.addWidget(a)
    stack.addWidget(b)
    stack.show()
    _spin(0.05)
    tr = A.page_transition(stack, b, 1)
    assert tr is not None and stack.currentWidget() is b
    t0 = time.perf_counter()
    for _ in range(10):
        tr.t = 0.5
        tr.grab()
    per_frame = (time.perf_counter() - t0) / 10 * 1000
    assert per_frame < 12, f"кадр перехода {per_frame:.1f} мс — на 60 к/с не успеть"
    _spin(A.PAGE_MS / 1000 + 0.25)
    assert not stack.findChildren(A.PageTransition) and not _live_anims(stack)
    assert A.page_transition(stack, b) is None                        # тот же экран — без перехода


def test_highlight_slides_and_stops():
    host = QWidget()
    host.resize(100, 600)
    host.show()
    hl = A.SlidingHighlight(host, "#0c1c1b", "#3fd0bd")
    hl.move_to(QRect(8, 10, 70, 54), animate=False)
    assert hl.geometry() == QRect(8, 10, 70, 54)
    hl.move_to(QRect(8, 300, 70, 54))
    seen = set()
    end = time.monotonic() + 0.6
    while time.monotonic() < end and hl._anim is not None:
        app.processEvents()
        seen.add(hl.y())
        time.sleep(0.005)
    assert any(10 < y < 300 for y in seen), f"подсветка прыгнула, а не поехала: {sorted(seen)}"
    hl.move_to(QRect(8, 400, 70, 54))                                 # передумали посреди — без падения
    _spin(A.HIGHLIGHT_MS / 1000 + 0.2)
    assert hl.geometry() == QRect(8, 400, 70, 54) and hl._anim is None and not _live_anims(host)


def test_toggle_slides():
    from ui_kit import Toggle
    t = Toggle()
    t.show()
    t.setChecked(False)
    _spin(0.3)
    t.click()
    _spin(0.05)
    assert 0 < t._t < 1                                               # едет, а не прыгает
    _spin(0.3)
    assert t._t == 1.0 and t.isChecked()
    t.grab()


def test_ripple_layer_one_per_window_and_goes_quiet():
    """Волна рисуется одним слоем окна; на нажатие ничего не создаётся.
    Живой случай: кнопка «Готово» удаляет себя сразу после нажатия — раньше
    это роняло программу при выходе."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    win = QWidget()
    win.resize(400, 300)
    b = QPushButton("Сохранить", win)
    b.setGeometry(20, 20, 160, 36)
    gone = QPushButton("Готово", win)
    gone.setGeometry(20, 80, 160, 36)
    gone.clicked.connect(gone.deleteLater)
    win.show()
    layer = A.install_click_ripple(win)
    assert A.install_click_ripple(win) is layer                     # один на окно
    late = QPushButton("Позже", win)                                  # появилась после — подключаем track()
    late.setGeometry(20, 140, 160, 36)
    late.show()
    layer.track(win)
    before = len(win.findChildren(QWidget))
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    QTest.mouseClick(gone, Qt.MouseButton.LeftButton)
    QTest.mouseClick(late, Qt.MouseButton.LeftButton)
    assert len(layer.waves) == 3 and layer.isVisible()
    assert len(win.findChildren(QWidget)) == before                  # нажатия не плодят виджеты
    _spin(0.05)
    layer.grab()
    _spin(A.RIPPLE_MS / 1000 + 0.2)
    assert not layer.waves and not layer.isVisible()                 # угасло и больше не крутится
    assert layer._anim.state() == QVariantAnimation.State.Stopped
    win.resize(500, 400)
    _spin(0.02)
    assert layer.geometry() == win.rect()
    win.deleteLater()
    _spin(0.05)
