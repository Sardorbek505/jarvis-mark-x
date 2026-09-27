"""Общие детали окон Джарвиса (окно «Свои команды», «Ключи и подключения»):
стиль, иконка-бейдж, переключатель, строка с переносом, подписи.

Палитра — ui.C, как у главного окна: почти чёрные панели, тонкие рамки,
бирюзовый акцент. Новое окно берёт всё отсюда — и выглядит так же."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QFrame, QLabel, QLayout, QPushButton, QWidget

from ui import C
from ui_icons import draw_icon, qicon


def plural(n: int, forms=("шаг", "шага", "шагов")) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


# ── стиль ────────────────────────────────────────────────────────────────────
STYLE = f"""
QDialog {{ background: {C.BG}; }}
QWidget {{ color: {C.TEXT}; font-family: 'Segoe UI'; font-size: 13px; }}
QLabel {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QWidget#canvas {{ background: {C.BG}; }}
QLineEdit, QComboBox {{ background: {C.DARK}; color: {C.WHITE}; border: 1px solid {C.BORDER};
  border-radius: 8px; padding: 7px 10px; selection-background-color: {C.BORDER_B}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {C.PRI_DIM}; }}
QLineEdit:disabled {{ color: {C.TEXT_DIM}; }}
QLineEdit#title {{ background: transparent; border: 1px solid transparent; font-size: 22px;
  font-weight: 600; padding: 2px 4px; }}
QLineEdit#title:hover {{ border-color: {C.BORDER}; }}
QLineEdit#title:focus {{ border-color: {C.PRI_DIM}; background: {C.DARK}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {C.PANEL2}; border: 1px solid {C.BORDER_B}; outline: none;
  selection-background-color: {C.PRI_GHO}; selection-color: {C.PRI}; padding: 4px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 10px; padding: 10px 8px; margin: 2px 0; color: {C.TEXT}; }}
QListWidget::item:hover {{ background: {C.PANEL2}; }}
QListWidget::item:selected {{ background: {C.PRI_GHO}; color: {C.WHITE}; }}
QPushButton {{ background: transparent; color: {C.TEXT_MED}; border: 1px solid {C.BORDER_B};
  border-radius: 8px; padding: 8px 14px; }}
QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; background: {C.PRI_GHO}; }}
QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
QPushButton#primary {{ background: {C.PRI}; color: {C.BG}; border: none; font-weight: 700; }}
QPushButton#primary:hover {{ background: #5fe0cf; }}
QPushButton#primary:disabled {{ background: {C.PRI_DIM}; color: {C.PANEL}; }}
QPushButton#ghost {{ border: none; padding: 4px; border-radius: 6px; }}
QPushButton#ghost:hover {{ background: {C.PRI_GHO}; }}
QPushButton#danger {{ color: {C.TEXT_DIM}; border: 1px solid {C.BORDER}; }}
QPushButton#danger:hover {{ color: {C.RED}; border-color: {C.RED}; background: #1a0a0e; }}
QPushButton#seg {{ border: none; border-radius: 8px; padding: 7px 16px; color: {C.TEXT_MED}; }}
QPushButton#seg:hover {{ color: {C.WHITE}; background: transparent; }}
QPushButton#seg:checked {{ background: {C.PANEL2}; color: {C.PRI}; }}
QPushButton#day {{ border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 5px 0; min-width: 34px;
  color: {C.TEXT_DIM}; font-size: 12px; }}
QPushButton#day:checked {{ background: {C.PRI_GHO}; border-color: {C.PRI_DIM}; color: {C.PRI}; }}
QPushButton#add {{ border: 1px dashed {C.BORDER_B}; color: {C.TEXT_MED}; padding: 11px; border-radius: 12px; }}
QPushButton#add:hover {{ border-color: {C.PRI_DIM}; color: {C.PRI}; }}
QMenu {{ background: {C.PANEL2}; border: 1px solid {C.BORDER_B}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 20px 8px 10px; border-radius: 6px; color: {C.TEXT}; }}
QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
QToolTip {{ background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER_B}; padding: 4px 8px; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {C.PRI_DIM}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QFrame#card {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 14px; }}
QFrame#ai {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI_DIM}; border-radius: 14px; }}
QFrame#step {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 12px; }}
QFrame#step:hover {{ border-color: {C.BORDER_B}; }}
QFrame#chip {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI_DIM}; border-radius: 15px; }}
QFrame#sidebar {{ background: {C.PANEL}; border: none; border-right: 1px solid {C.BORDER}; }}
QFrame#bar {{ background: {C.PANEL}; border: none; }}
QFrame#seg {{ background: {C.DARK}; border: 1px solid {C.BORDER}; border-radius: 10px; }}
QLabel#cap {{ color: {C.TEXT_DIM}; font-family: Consolas; font-size: 11px; font-weight: bold; }}
QLabel#hint {{ color: {C.TEXT_DIM}; font-size: 12px; }}
QLabel#h1 {{ color: {C.WHITE}; font-size: 17px; font-weight: 700; }}
QLabel#h2 {{ color: {C.WHITE}; font-size: 15px; font-weight: 600; }}
QLabel#brand {{ color: {C.PRI}; font-family: Consolas; font-size: 11px; font-weight: bold; }}
QLabel#stepTitle {{ color: {C.TEXT_MED}; font-size: 12px; font-weight: 600; }}
QLabel#status {{ color: {C.TEXT_MED}; font-size: 12px; }}
"""


def _label(text: str, name: str = "", wrap: bool = True) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def _cap(text: str) -> QLabel:
    """Подпись раздела капсом, с разрядкой — как в HUD."""
    w = _label(text.upper(), "cap", wrap=False)
    f = w.font()
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
    w.setFont(f)
    return w


def _icon_btn(icon: str, tip: str, size: int = 14, color: str = C.TEXT_MED) -> QPushButton:
    b = QPushButton()
    b.setObjectName("ghost")
    b.setIcon(qicon(icon, size, color))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFixedSize(28, 28)
    return b


class IconBadge(QWidget):
    """Иконка в скруглённом квадрате — как в настройках iPhone, в цветах Джарвиса."""

    def __init__(self, icon: str, size: int = 32, parent=None):
        super().__init__(parent)
        self.icon = icon
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(C.PRI_DIM), 1))
        p.setBrush(QColor(C.PRI_GHO))
        p.drawRoundedRect(r, self.width() * 0.28, self.width() * 0.28)
        draw_icon(p, self.icon, r.center(), self.width() * 0.5, QColor(C.PRI))


class Toggle(QAbstractButton):
    """Переключатель вкл/выкл."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.PRI if on else C.BORDER_B))
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        p.setBrush(QColor(C.BG if on else C.TEXT_MED))
        p.drawEllipse(QPointF(29 if on else 11, 11), 8, 8)


class FlowLayout(QLayout):
    """Элементы в строку с переносом — для чипсов фраз."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _layout(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line > 0:
                x, y, line = rect.x(), y + line + sp, 0
            if not test:
                it.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x += hint.width() + sp
            line = max(line, hint.height())
        return y + line - rect.y()


def _small_icon(name: str, size: int = 12, color: str = C.PRI) -> QLabel:
    lbl = QLabel()
    lbl.setPixmap(qicon(name, size, color).pixmap(size, size))
    return lbl


def _line() -> QFrame:
    ln = QFrame()
    ln.setFixedHeight(1)
    ln.setStyleSheet(f"background: {C.BORDER}; border: none;")
    return ln


class Progress(QWidget):
    """Тонкая полоска «подключено N из M»."""

    def __init__(self):
        super().__init__()
        self.value = 0.0
        self.setFixedHeight(4)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C.BORDER))
        p.drawRoundedRect(QRectF(self.rect()), 2, 2)
        p.setBrush(QColor(C.PRI))
        p.drawRoundedRect(QRectF(0, 0, self.width() * self.value, self.height()), 2, 2)
