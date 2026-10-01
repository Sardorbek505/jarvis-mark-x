"""Общие детали окон Джарвиса (окно «Свои команды», «Ключи и подключения»):
стиль, иконка-бейдж, переключатель, строка с переносом, подписи.

Палитра — ui.C, как у главного окна: почти чёрные панели, тонкие рамки,
бирюзовый акцент. Новое окно берёт всё отсюда — и выглядит так же."""
from __future__ import annotations

from PyQt6.QtCore import QRect, QRectF, QSize, Qt
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
def _chevron_png() -> str:
    """Стрелка выпадающего списка — файлом: Qt-стили берут картинку только по пути.
    Без неё списки выглядели как поля ввода, и никто не догадывался их открыть."""
    import tempfile
    from pathlib import Path
    path = Path(tempfile.gettempdir()) / "jarvis_ui_chevron_v1.png"
    if not path.is_file():
        try:
            from PyQt6.QtGui import QImage
            img = QImage(28, 28, QImage.Format.Format_ARGB32)
            img.fill(0)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(C.TEXT_MED), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            from PyQt6.QtCore import QPointF
            p.drawPolyline([QPointF(8, 11), QPointF(14, 17), QPointF(20, 11)])
            p.end()
            img.save(str(path))
        except Exception:
            return ""
    return path.as_posix()


_CHEVRON = _chevron_png()

STYLE = f"""
QDialog {{ background: {C.BG}; }}
QWidget {{ color: {C.TEXT}; font-family: 'Segoe UI'; font-size: 13px; }}
QLabel {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QWidget#canvas {{ background: {C.BG}; }}
QLineEdit, QComboBox {{ background: {C.DARK}; color: {C.WHITE}; border: 1px solid {C.BORDER_B};
  border-radius: 10px; padding: 7px 10px; selection-background-color: {C.BORDER_B}; }}
QComboBox {{ padding-right: 30px; }}
QComboBox:hover {{ border-color: {C.PRI_DIM}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {C.PRI_DIM}; }}
QLineEdit:disabled {{ color: {C.TEXT_DIM}; }}
QLineEdit#title {{ background: transparent; border: 1px solid transparent; font-size: 22px;
  font-weight: 600; padding: 2px 4px; }}
QLineEdit#title:hover {{ border-color: {C.BORDER}; }}
QLineEdit#title:focus {{ border-color: {C.PRI_DIM}; background: {C.DARK}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{_CHEVRON}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {C.PANEL2}; border: 1px solid {C.BORDER_B}; outline: none;
  selection-background-color: {C.PRI_GHO}; selection-color: {C.PRI}; padding: 4px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 10px; padding: 10px 8px; margin: 2px 0; color: {C.TEXT}; }}
QListWidget::item:hover {{ background: {C.PANEL2}; }}
QListWidget::item:selected {{ background: {C.PRI_GHO}; color: {C.WHITE}; }}
QPushButton {{ background: transparent; color: {C.TEXT}; border: 1px solid {C.BORDER_B};
  border-radius: 10px; padding: 8px 14px; }}
QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; background: {C.PRI_GHO}; }}
QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
QPushButton#primary {{ background: {C.PRI}; color: {C.BG}; border: none; font-weight: 700; }}
QPushButton#primary:hover {{ background: #5fe0cf; }}
QPushButton#primary:disabled {{ background: {C.BORDER_B}; color: {C.TEXT_DIM}; }}
QPushButton#ghost {{ border: none; padding: 4px; border-radius: 6px; }}
QPushButton#ghost:hover {{ background: {C.PRI_GHO}; }}
QPushButton#danger {{ color: {C.RED}; border: 1px solid #4a1f28; }}
QPushButton#danger:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; background: transparent; }}
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
QFrame#card {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 16px; }}
QFrame#card:hover {{ border-color: {C.BORDER_A}; }}
QFrame#ai {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI_DIM}; border-radius: 14px; }}
QFrame#step {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 12px; }}
QFrame#step:hover {{ border-color: {C.BORDER_B}; }}
QFrame#chip {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI_DIM}; border-radius: 15px; }}
QFrame#sidebar {{ background: {C.PANEL}; border: none; border-right: 1px solid {C.BORDER}; }}
QFrame#bar {{ background: {C.PANEL}; border: none; }}
QFrame#seg {{ background: {C.DARK}; border: 1px solid {C.BORDER}; border-radius: 10px; }}
QLabel#cap {{ color: {C.TEXT_MED}; font-size: 13px; font-weight: 600; }}
QLabel#hint {{ color: {C.TEXT_DIM}; font-size: 12.5px; }}
QLabel#h1 {{ color: {C.WHITE}; font-size: 17px; font-weight: 700; }}
QLabel#h2 {{ color: {C.WHITE}; font-size: 15px; font-weight: 600; }}
QLabel#brand {{ color: {C.PRI}; font-size: 12px; font-weight: 600; }}
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
    """Подпись раздела — как в настройках macOS: обычными буквами, полужирным.
    Капс с разрядкой читался тяжело и делал окна мрачными."""
    text = text.strip()
    if text.isupper() and len(text) > 1:           # «ГОЛОС» из старых вызовов → «Голос»
        text = text[0] + text[1:].lower()
    return _label(text, "cap", wrap=False)


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


def _mix(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t))


class Toggle(QAbstractButton):
    """Переключатель вкл/выкл: кружок переезжает, цвет перетекает, на ходу чуть вытягивается."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._t = 0.0
        self._anim = None
        self.toggled.connect(self._animate)

    def setChecked(self, on: bool):             # из кода — сразу, без анимации
        super().setChecked(on)
        if self._anim is None:
            self._t = 1.0 if on else 0.0
            self.update()

    def _animate(self, on: bool):
        from ui_anim import animate_value, enabled
        if not self.isVisible() or not enabled():
            self._t = 1.0 if on else 0.0
            self.update()
            return
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        self._anim = animate_value(self, self._t, 1.0 if on else 0.0, 220, self._step,
                                   on_done=self._done)

    def _step(self, v):
        self._t = float(v)
        self.update()

    def _done(self):
        self._anim = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = max(0.0, min(1.0, self._t))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_mix(C.BORDER_B, C.PRI, t))
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        p.setBrush(_mix(C.TEXT_MED, C.BG, t))
        stretch = 3.0 * (1 - abs(2 * t - 1))       # в середине пути кружок вытянут, как капля
        x = 11 + 18 * t
        p.drawRoundedRect(QRectF(x - 8 - stretch / 2, 3, 16 + stretch, 16), 8, 8)


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


# ── графика (assets/art, scripts/build_art.py) ───────────────────────────────
def art_path(rel: str):
    """Путь к картинке из assets/art (в exe — рядом с программой); нет файла — None."""
    from core.paths import get_base_dir
    p = get_base_dir() / "assets" / "art" / rel
    return p if p.is_file() else None


_ART_CACHE: dict = {}


def art_pixmap(rel: str):
    from PyQt6.QtGui import QPixmap
    if rel not in _ART_CACHE:
        p = art_path(rel)
        pm = QPixmap(str(p)) if p else QPixmap()
        _ART_CACHE[rel] = None if pm.isNull() else pm
    return _ART_CACHE[rel]


class EmptyArt(QWidget):
    """Пустой список: тусклая картинка (assets/art/empty) и подсказка под ней."""

    def __init__(self, name: str, text: str, parent=None, width: int = 200):
        super().__init__(parent)
        from PyQt6.QtWidgets import QVBoxLayout
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(4)
        pm = art_pixmap(f"empty/empty_{name}.png")
        if pm is not None:
            img = QLabel()
            dpr = 2
            scaled = pm.scaledToWidth(width * dpr, Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            img.setPixmap(scaled)
            img.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lay.addWidget(img)
        self.text = _label(text, "hint")
        self.text.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.text)
        lay.addStretch(1)


class ArtBanner(QWidget):
    """Иллюстрация шага (assets/art/setup, 1200×720, герой справа): кадрируется
    по ширине, слева затемняется под заголовок и подпись."""

    def __init__(self, rel: str, title: str = "", text: str = "", height: int = 132, parent=None):
        super().__init__(parent)
        self.pm = art_pixmap(rel)
        self.title, self.body = title, text
        self.setFixedHeight(height)

    def paintEvent(self, _):
        from PyQt6.QtGui import QLinearGradient, QPainterPath
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.setClipPath(path)
        p.fillRect(r, QColor(C.BG))
        if self.pm is not None:
            # Высота картинки = высота баннера × 1.6: герой крупнее, центр по вертикали.
            h = r.height() * 1.6
            w = h * self.pm.width() / self.pm.height()
            x = max(r.width() - w, r.width() * 0.55 - w * 0.5)
            p.drawPixmap(QRectF(x, (r.height() - h) / 2, w, h), self.pm, QRectF(self.pm.rect()))
            g = QLinearGradient(r.topLeft(), r.topRight())
            g.setColorAt(0.0, QColor(3, 6, 9, 255))
            g.setColorAt(0.42, QColor(3, 6, 9, 200))
            g.setColorAt(0.75, QColor(3, 6, 9, 0))
            p.fillRect(r, g)
        p.setClipping(False)
        p.setPen(QPen(QColor(C.BORDER_B), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if self.title:
            f = QFont("Segoe UI", 1)
            f.setPointSizeF(15)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(C.WHITE))
            p.drawText(QRectF(22, 0, r.width() * 0.56, r.height() / 2 + 4),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom), self.title)
        if self.body:
            f = QFont("Segoe UI", 1)
            f.setPointSizeF(9.5)
            p.setFont(f)
            p.setPen(QColor(C.TEXT_MED))
            p.drawText(QRectF(22, r.height() / 2 + 8, r.width() * 0.56, r.height() / 2 - 12),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                       self.body)
