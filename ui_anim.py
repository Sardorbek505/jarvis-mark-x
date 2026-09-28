"""Анимации окна Джарвиса — красиво, но без лагов.

Правила, из-за которых они не тормозят:
  • переход между экранами — два СНИМКА (QPixmap), а не эффекты на живых
    виджетах: QGraphicsOpacityEffect на дереве виджетов перерисовывает его
    целиком каждый кадр, снимок рисуется за доли миллисекунды;
  • ничего не крутится впустую: анимация идёт 0.2–0.4 с и останавливается,
    постоянных таймеров нет;
  • всё через QVariantAnimation (тот же таймер Qt, 60 к/с), без sleep и потоков.

Что есть:
  PageTransition — экран уезжает и растворяется, новый выплывает навстречу;
  SlidingHighlight — подсветка в левой панели переезжает к выбранному экрану;
  RippleLayer — волна от места нажатия на кнопке (один слой на окно);
  animate_value — плавное число 0..1 для своих виджетов (переключатель и т.п.).
"""
from __future__ import annotations

import atexit
import os
import weakref

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRect, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QAbstractButton, QWidget

PAGE_MS = 260


def enabled() -> bool:
    """«Настройки» → «Анимации» (JARVIS_ANIMATIONS=0 — выключены: слабый ПК или не нравятся)."""
    return os.getenv("JARVIS_ANIMATIONS", "1") != "0"

HIGHLIGHT_MS = 300
RIPPLE_MS = 420


def animate_value(owner: QObject, start: float, end: float, ms: int, on_value,
                  curve=QEasingCurve.Type.OutCubic, on_done=None) -> QVariantAnimation:
    """Число от start до end за ms — в on_value каждый кадр. Анимация живёт у owner."""
    a = QVariantAnimation(owner)
    a.setStartValue(float(start))
    a.setEndValue(float(end))
    a.setDuration(ms)
    a.setEasingCurve(curve)
    a.valueChanged.connect(on_value)
    if on_done:
        a.finished.connect(on_done)
    _running.add(a)
    a.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return a


# Незаконченные анимации при выходе: Python удалял бы их уже после того, как Qt
# разобрал общий таймер анимаций, — падение на закрытии. Останавливаем заранее.
_running: "weakref.WeakSet[QVariantAnimation]" = weakref.WeakSet()


def stop_all():
    for a in list(_running):
        try:
            if a.state() != QVariantAnimation.State.Stopped:
                a.stop()
        except RuntimeError:
            pass                               # уже удалена Qt
    _running.clear()


atexit.register(stop_all)


class PageTransition(QWidget):
    """Поверх стопки экранов: старый снимок уходит, новый приходит. Сам себя убирает."""

    def __init__(self, host: QWidget, old: QPixmap, new: QPixmap, direction: int = 1, ms: int = PAGE_MS):
        super().__init__(host)
        self.old, self.new, self.dir = old, new, direction
        self.t = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setGeometry(host.rect())
        self.raise_()
        self.show()
        self.anim = animate_value(self, 0.0, 1.0, ms, self._step, on_done=self._done)

    def _step(self, v):
        self.t = float(v)
        self.update()

    def _done(self):
        self.hide()
        self.deleteLater()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)   # сдвиг на целые пиксели — чётко и быстро
        t = self.t
        shift = 22 * self.dir
        p.fillRect(self.rect(), QColor(3, 6, 9))
        # старый: чуть уезжает и гаснет быстрее, чем приходит новый
        p.setOpacity(max(0.0, 1.0 - t * 1.6))
        p.drawPixmap(0, int(-shift * t * 0.5), self.old)
        p.setOpacity(min(1.0, t * 1.25))
        p.drawPixmap(0, int(shift * (1.0 - t)), self.new)


def page_transition(stack, new_widget: QWidget, direction: int = 1) -> PageTransition | None:
    """Переключить QStackedWidget на new_widget с переходом. Без экрана (тесты) — сразу."""
    old = stack.currentWidget()
    if (old is None or old is new_widget or not stack.isVisible() or stack.width() < 10
            or not enabled()):
        stack.setCurrentWidget(new_widget)
        return None
    old_pm = old.grab()
    stack.setCurrentWidget(new_widget)
    new_widget.resize(stack.size())
    new_pm = new_widget.grab()
    return PageTransition(stack, old_pm, new_pm, direction)


class SlidingHighlight(QWidget):
    """Подложка выбранного пункта в левой панели: переезжает, а не прыгает."""

    def __init__(self, parent: QWidget, fill: str, accent: str):
        super().__init__(parent)
        self.fill, self.accent = QColor(fill), QColor(accent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._anim: QVariantAnimation | None = None
        self._from = QRect()
        self._to = QRect()
        self.hide()

    def move_to(self, rect: QRect, animate: bool = True):
        if rect.isNull():
            return
        self.lower()
        if not self.isVisible() or not animate or not enabled():
            self.setGeometry(rect)
            self.show()
            return
        if self._anim is not None:
            self._anim.stop()                  # удалится сам (DeleteWhenStopped) — ссылку бросаем
            self._anim = None
        self._from, self._to = self.geometry(), rect
        self._anim = animate_value(self, 0.0, 1.0, HIGHLIGHT_MS, self._step,
                                   curve=QEasingCurve.Type.OutBack, on_done=self._done)

    def _done(self):
        self._anim = None

    def _step(self, v):
        v = float(v)
        f, t = self._from, self._to
        lerp = lambda a, b: round(a + (b - a) * v)  # noqa: E731
        self.setGeometry(QRect(lerp(f.x(), t.x()), lerp(f.y(), t.y()), lerp(f.width(), t.width()),
                               lerp(f.height(), t.height())))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.fill)
        p.drawRoundedRect(r, 10, 10)
        g = QColor(self.accent)                                    # светящаяся полоска слева
        p.setBrush(g)
        p.drawRoundedRect(QRectF(r.x() + 1, r.center().y() - 11, 3, 22), 1.5, 1.5)
        g.setAlpha(60)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(r.x(), r.center().y() - 14, 7, 28), 3.5, 3.5)


class RippleLayer(QWidget):
    """Волны от нажатий — ОДИН постоянный прозрачный слой поверх окна.

    Раньше на каждое нажатие внутри фильтра событий создавался виджет-волна на
    самой кнопке. Когда кнопка тут же удалялась («Готово» закрывает экран), PyQt
    терял его след, и программа падала при выходе. Теперь на нажатие ничего не
    создаётся: слой запоминает точку и рисует волну, пока она не угаснет."""

    def __init__(self, window: QWidget, color: str = "#3fd0bd"):
        super().__init__(window)
        self.win = window
        self.color = QColor(color)
        self.waves: list[list] = []          # [центр, прямоугольник кнопки, t]
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setGeometry(window.rect())
        self._anim: QVariantAnimation | None = None
        window.installEventFilter(self)                          # размер окна
        # Кнопки подключаем поштучно (track), а не фильтром на всё приложение:
        # фильтр приложения из Python видит КАЖДЫЙ объект Qt, и при выходе PyQt
        # падал на обёртках уже удалённых внутренних объектов.
        self.track(window)
        self.hide()

    def track(self, root: QWidget):
        """Подключить волну к кнопкам внутри root (новый экран, новые строки)."""
        for b in root.findChildren(QAbstractButton):
            if not b.property("_jarvis_ripple"):
                b.setProperty("_jarvis_ripple", True)
                b.installEventFilter(self)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Resize and obj is self.win:
            self.setGeometry(self.win.rect())
        elif (t == QEvent.Type.MouseButtonPress and enabled() and isinstance(obj, QAbstractButton)
              and obj.isEnabled() and obj.width() > 24 and obj.height() > 16):
            top_left = obj.mapTo(self.win, obj.rect().topLeft())
            rect = QRectF(top_left.x(), top_left.y(), obj.width(), obj.height())
            center = QPointF(top_left.x() + ev.position().x(), top_left.y() + ev.position().y())
            self.waves.append([center, rect, 0.0])
            self._run()
        return False

    def _run(self):
        self.raise_()
        self.show()
        if self._anim is None:
            self._anim = QVariantAnimation(self)              # своя, не удаляется — живёт вместе со слоем
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.setDuration(1000)
            self._anim.setLoopCount(-1)
            self._anim.valueChanged.connect(self._tick)
        if self._anim.state() != QVariantAnimation.State.Running:
            self._last = None
            self._anim.start()

    def _tick(self, _v):
        import time
        now = time.monotonic()
        dt = 0.0 if self._last is None else now - self._last
        self._last = now
        for w in self.waves:
            w[2] += dt * 1000 / RIPPLE_MS
        self.waves = [w for w in self.waves if w[2] < 1.0]
        if not self.waves:
            self._anim.stop()                                  # тишина — не крутимся впустую
            self.hide()
        self.update()

    def paintEvent(self, _):
        if not self.waves:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for center, rect, t in self.waves:
            rad = 6 + (rect.width() ** 2 + rect.height() ** 2) ** 0.5 * t
            g = QRadialGradient(center, rad)
            c = QColor(self.color)
            c.setAlpha(int(70 * (1 - t)))
            g.setColorAt(0.0, c)
            c.setAlpha(0)
            g.setColorAt(1.0, c)
            p.setBrush(g)
            p.drawRoundedRect(rect, 8, 8)


def install_click_ripple(window: QWidget, color: str = "#3fd0bd") -> RippleLayer | None:
    """Волны от нажатий на кнопках этого окна. Слой — ребёнок окна и умирает вместе с ним."""
    if not enabled():
        return None
    layer = getattr(window, "_ripple_layer", None)
    if layer is None:
        layer = RippleLayer(window, color)
        window._ripple_layer = layer
    return layer
