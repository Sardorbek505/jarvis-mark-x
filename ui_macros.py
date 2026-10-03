"""Окно «Свои команды».

Как им пользоваться — видно сразу, без объяснений:
  1. Слева — ВАШИ команды (паки программ — на своей странице, не в куче).
  2. Справа сверху — «Опишите словами» → «Собрать»: ИИ раскладывает на шаги.
  3. Ниже — команда как рецепт: название, фразы-чипсы («включи режим
     стрима» ×), шаги лентой сверху вниз — у каждого номер, иконка и одно
     понятное поле. Сложные действия выбираются из списка, без JSON.
  4. «Когда запускать сам» — по времени и дням, при открытии программы,
     при запуске Джарвиса (необязательно).
  5. Всё сохраняется само, как только команда собрана (есть фраза или
     условие и хотя бы один шаг); внизу — «Проверить» и «Удалить».

Стиль — Джарвиса (палитра ui.C): почти чёрные панели, тонкие рамки,
бирюзовый акцент, подписи капсом, векторные иконки ui_icons.
Сами команды живут в core/macros.py; окно только редактирует их.
"""
from __future__ import annotations

import json
import logging
import threading

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea,
                             QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from core import macro_triggers as mt
from core import macros as mc
from core.macro_packs import PACKS
from ui import C
from ui_icons import qicon
from ui_kit import STYLE, Autosave, EmptyArt, FlowLayout, IconBadge, SavedNote, Toggle, _cap, _icon_btn, _label, _line, _small_icon, plural

logger = logging.getLogger(__name__)

# ── что умеет шаг: иконка, название, подсказка поля ─────────────────────────
STEP_META = {
    "open_app": ("app", "Запустить программу", "Имя программы — OBS Studio, Telegram, Steam"),
    "open_url": ("globe", "Открыть сайт", "Адрес — youtube.com"),
    "keys": ("keyboard", "Нажать клавиши", "Сочетание — ctrl+shift+s, f5, alt+tab"),
    "type": ("text", "Набрать текст", "Что напечатать — можно {слово} из фразы"),
    "click": ("cursor", "Клик мышью", ""),
    "wait": ("timer", "Подождать", "Секунды — 2"),
    "volume": ("volume", "Громкость системы", "Уровень 0–100"),
    "media": ("play", "Медиаклавиша", ""),
    "say": ("speak", "Сказать вслух", "Фраза Джарвиса"),
    "tool": ("bolt", "Действие Джарвиса", ""),
}
STEP_ORDER = ["open_app", "wait", "keys", "tool", "open_url", "type", "volume", "media", "say", "click"]
MEDIA = [("playpause", "Пауза / играть"), ("next", "Следующий трек"), ("previous", "Предыдущий трек"),
         ("mute", "Выключить звук")]

# Действия Джарвиса — по-человечески. (подпись, инструмент, аргументы, поле-значение, подсказка)
ACTIONS = [
    ("Включить музыку", "music_player", {"action": "play"}, "query", "Что включить — lofi, Believer"),
    ("Музыка на паузу", "music_player", {"action": "pause"}, "", ""),
    ("Музыку дальше", "music_player", {"action": "next"}, "", ""),
    ("Громкость музыки", "music_player", {"action": "volume_set"}, "value", "Уровень 0–100"),
    ("Ролик на YouTube", "youtube_player", {"action": "play"}, "query", "Что найти"),
    ("Фильм", "movie_player", {"action": "play"}, "title", "Название фильма"),
    ("Таймер", "clock", {"action": "timer_set"}, "minutes", "Минуты — 10"),
    ("Погода", "weather", {}, "", ""),
    ("Свернуть все окна", "window_control", {"action": "minimize_all"}, "", ""),
    ("Смотреть на экран", "eyes", {"action": "open", "source": "screen"}, "", ""),
]


def _action_of(step: dict) -> int | None:
    """Какой пункт ACTIONS описывает шаг-инструмент (None — свой, вручную)."""
    args = dict(step.get("args") or {})
    for i, (_t, tool, base, field, _h) in enumerate(ACTIONS):
        if step.get("tool") != tool:
            continue
        if {k: v for k, v in args.items() if k != field} == base:
            return i
    return None


class Chip(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text
        self.setObjectName("chip")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 3, 3, 3)
        lay.setSpacing(2)
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {C.WHITE};")
        x = _icon_btn("close", "Убрать фразу", 10)
        x.setFixedSize(24, 24)
        x.clicked.connect(lambda: self.removed.emit(self.text))
        lay.addWidget(_small_icon("mic"))
        lay.addWidget(lbl)
        lay.addWidget(x)


class StepRow(QWidget):
    """Шаг: номер на ленте слева, карточка справа — иконка, название, поле."""

    moved = pyqtSignal(object, int)
    removed = pyqtSignal(object)

    def __init__(self, step: dict, parent=None):
        super().__init__(parent)
        self.kind = step["do"]
        self.number, self.last = 1, True
        icon, title, hint = STEP_META[self.kind]
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.rail = _Rail(self)
        row.addWidget(self.rail)

        card = QFrame()
        card.setObjectName("step")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 10, 6, 10)
        lay.setSpacing(12)
        lay.addWidget(IconBadge(icon), 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(6)
        body.addWidget(_label(title, "stepTitle"))
        self.fields = QHBoxLayout()
        self.fields.setSpacing(8)
        body.addLayout(self.fields)
        lay.addLayout(body, 1)
        tools = QHBoxLayout()
        tools.setSpacing(0)
        up, down = _icon_btn("up", "Выше"), _icon_btn("down", "Ниже")
        rm = _icon_btn("trash", "Убрать шаг", 14, C.TEXT_DIM)
        up.clicked.connect(lambda: self.moved.emit(self, -1))
        down.clicked.connect(lambda: self.moved.emit(self, 1))
        rm.clicked.connect(lambda: self.removed.emit(self))
        for b in (up, down, rm):
            tools.addWidget(b)
        lay.addLayout(tools)
        lay.setAlignment(tools, Qt.AlignmentFlag.AlignTop)
        row.addWidget(card, 1)
        self._build_fields(step, hint)

    def _build_fields(self, step: dict, hint: str):
        v = str(step.get("value", ""))
        self.value = self.x = self.y = self.combo = self.args = None
        if self.kind == "click":
            self.x, self.y = QLineEdit(str(step.get("x", 0))), QLineEdit(str(step.get("y", 0)))
            self.combo = QComboBox()
            for key, text in (("left", "Левой кнопкой"), ("right", "Правой кнопкой"), ("double", "Двойной")):
                self.combo.addItem(text, key)
            self.combo.setCurrentIndex(max(0, self.combo.findData(v or "left")))
            for w, ph in ((self.x, "X"), (self.y, "Y")):
                w.setPlaceholderText(ph)
                w.setFixedWidth(76)
                self.fields.addWidget(w)
            self.fields.addWidget(self.combo)
            self.fields.addStretch(1)
        elif self.kind == "media":
            self.combo = QComboBox()
            for key, text in MEDIA:
                self.combo.addItem(text, key)
            self.combo.setCurrentIndex(max(0, self.combo.findData(v or "playpause")))
            self.fields.addWidget(self.combo, 1)
        elif self.kind == "tool":
            self.combo = QComboBox()
            for title, *_ in ACTIONS:
                self.combo.addItem(title)
            self.combo.addItem("Другое — вручную")
            self.value, self.args = QLineEdit(), QLineEdit()
            self.args.setPlaceholderText('инструмент {"action": "…"}')
            i = _action_of(step)
            if i is None:
                self.combo.setCurrentIndex(len(ACTIONS))
                self.args.setText(f"{step.get('tool', '')} {json.dumps(step.get('args') or {}, ensure_ascii=False)}")
            else:
                self.combo.setCurrentIndex(i)
                field = ACTIONS[i][3]
                self.value.setText(str((step.get("args") or {}).get(field, "")) if field else "")
            self.combo.currentIndexChanged.connect(self._tool_changed)
            self.fields.addWidget(self.combo)
            self.fields.addWidget(self.value, 1)
            self.fields.addWidget(self.args, 1)
            self._tool_changed()
        else:
            self.value = QLineEdit(v)
            self.value.setPlaceholderText(hint)
            self.fields.addWidget(self.value, 1)

    def _tool_changed(self):
        i = self.combo.currentIndex()
        custom = i >= len(ACTIONS)
        self.args.setVisible(custom)
        hint = "" if custom else ACTIONS[i][4]
        self.value.setVisible(bool(hint))
        self.value.setPlaceholderText(hint)

    def read(self) -> dict:
        if self.kind == "click":
            return {"do": "click", "value": self.combo.currentData(),
                    "x": self.x.text().strip() or 0, "y": self.y.text().strip() or 0}
        if self.kind == "media":
            return {"do": "media", "value": self.combo.currentData()}
        if self.kind == "tool":
            i = self.combo.currentIndex()
            if i >= len(ACTIONS):
                tool, _, args = self.args.text().strip().partition(" ")
                return {"do": "tool", "tool": tool, "args": args or "{}", "value": ""}
            _t, tool, base, field, _h = ACTIONS[i]
            args = dict(base)
            if field and self.value.text().strip():
                args[field] = self.value.text().strip()
            return {"do": "tool", "tool": tool, "args": args, "value": ""}
        return {"do": self.kind, "value": self.value.text().strip()}


# Условия запуска без фразы: (иконка, название, подсказка)
WHEN_META = {
    "time": ("timer", "В определённое время", "Раз в день, в выбранные дни"),
    "app": ("app", "Когда открываю программу", "Имя процесса — obs, steam, chrome.exe"),
    "start": ("bolt", "При запуске Джарвиса", "Через несколько секунд после запуска"),
}


class WhenRow(QFrame):
    """Условие: иконка, название и поля — время с днями или программа."""

    removed = pyqtSignal(object)

    def __init__(self, when: dict, parent=None):
        super().__init__(parent)
        self.kind = when["on"]
        self.setObjectName("step")
        icon, title, hint = WHEN_META[self.kind]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 6, 10)
        lay.setSpacing(12)
        lay.addWidget(IconBadge(icon), 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(6)
        body.addWidget(_label(title, "stepTitle"))
        fields = QHBoxLayout()
        fields.setSpacing(6)
        self.at = self.app = None
        self.days: list[QPushButton] = []
        if self.kind == "time":
            self.at = QLineEdit(when.get("at") or "09:00")
            self.at.setPlaceholderText("09:00")
            self.at.setFixedWidth(72)
            fields.addWidget(self.at)
            fields.addSpacing(6)
            on = set(when.get("days") or range(7))
            for i, name in enumerate(mt.DAY_NAMES):
                b = QPushButton(name)
                b.setObjectName("day")
                b.setCheckable(True)
                b.setChecked(i in on)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                self.days.append(b)
                fields.addWidget(b)
            fields.addStretch(1)
        elif self.kind == "app":
            self.app = QLineEdit(when.get("app") or "")
            self.app.setPlaceholderText(hint)
            fields.addWidget(self.app, 1)
        else:
            fields.addWidget(_label(hint, "hint"), 1)
        body.addLayout(fields)
        lay.addLayout(body, 1)
        rm = _icon_btn("trash", "Убрать условие", 14, C.TEXT_DIM)
        rm.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(rm, 0, Qt.AlignmentFlag.AlignTop)

    def read(self) -> dict:
        if self.kind == "time":
            return {"on": "time", "at": self.at.text().strip(),
                    "days": [i for i, b in enumerate(self.days) if b.isChecked()]}
        if self.kind == "app":
            return {"on": "app", "app": self.app.text().strip()}
        return {"on": "start"}


class _Rail(QWidget):
    """Лента слева от шагов: кружок с номером и линия к соседям."""

    def __init__(self, row: StepRow):
        super().__init__(row)
        self.row = row
        self.setFixedWidth(26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 13, 27
        p.setPen(QPen(QColor(C.BORDER_B), 2))
        if self.row.number > 1:
            p.drawLine(QPointF(cx, -4), QPointF(cx, cy - 11))
        if not self.row.last:
            p.drawLine(QPointF(cx, cy + 11), QPointF(cx, self.height() + 4))
        p.setPen(QPen(QColor(C.PRI_DIM), 1.5))
        p.setBrush(QColor(C.BG))
        p.drawEllipse(QPointF(cx, cy), 11, 11)
        p.setPen(QColor(C.PRI))
        f = QFont("Consolas", 9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(cx - 11, cy - 11, 22, 22), int(Qt.AlignmentFlag.AlignCenter), str(self.row.number))


# ── окно ─────────────────────────────────────────────────────────────────────

class MacrosDialog(QDialog):
    _ai_done = pyqtSignal(object, str)

    def __init__(self, parent=None, store: mc.Macros | None = None, build=None):
        super().__init__(parent)
        self.store = store or mc.macros()
        self.build = build or mc.build_with_ai
        self.current: mc.Command | None = None
        self.step_rows: list[StepRow] = []
        self.when_rows: list[WhenRow] = []
        self._phrases: list[str] = []
        self._loading = False            # форма заполняется из записи — это не правка
        self._typing_phrase = False      # недопечатанную фразу автосохранение не забирает
        self.autosave = Autosave(self, self._autosave)
        self.setWindowTitle("ДЖАРВИС — свои команды")
        self.setStyleSheet(STYLE)
        self.resize(1080, 740)
        self.setMinimumSize(920, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(_line())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._commands_page())
        self.pages.addWidget(self._packs_page())
        root.addWidget(self.pages, 1)
        self._ai_done.connect(self._on_ai)
        self.reload()
        self.new_command()

    # ── шапка ────────────────────────────────────────────────────────────────
    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("bolt", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        brand = _label("Джарвис", "brand")
        f = brand.font()
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        brand.setFont(f)
        col.addWidget(brand)
        col.addWidget(_label("Свои команды", "h1"))
        col.addWidget(_label("Всё сохраняется сразу", "hint", wrap=False))
        lay.addLayout(col)
        lay.addStretch(1)
        self.saved = SavedNote()
        lay.addWidget(self.saved)
        seg = QFrame()
        seg.setObjectName("seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.seg_own = QPushButton("  Мои команды")
        self.seg_packs = QPushButton("  Паки программ")
        for b, icon, page in ((self.seg_own, "bolt", 0), (self.seg_packs, "grid", 1)):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setIcon(qicon(icon, 14, C.PRI))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=page: self.show_page(i))
            sl.addWidget(b)
        self.seg_own.setChecked(True)
        lay.addWidget(seg)
        return w

    def show_page(self, i: int):
        self.pages.setCurrentIndex(i)
        self.seg_own.setChecked(i == 0)
        self.seg_packs.setChecked(i == 1)

    # ── мои команды ─────────────────────────────────────────────────────────
    def _commands_page(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(290)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 18, 16, 16)
        sl.setSpacing(10)
        new = QPushButton("  Новая команда")
        new.setObjectName("primary")
        new.setIcon(qicon("plus", 14, C.BG))
        new.setCursor(Qt.CursorShape.PointingHandCursor)
        new.clicked.connect(self.new_command)
        sl.addWidget(new)
        self.search = QLineEdit(placeholderText="Найти команду")
        self.search.textChanged.connect(self.reload)
        sl.addWidget(self.search)
        sl.addSpacing(4)
        sl.addWidget(_cap("Мои команды"))
        self.cmd_list = QListWidget()
        self.cmd_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cmd_list.setWordWrap(True)
        self.cmd_list.setIconSize(QSize(16, 16))
        self.cmd_list.currentItemChanged.connect(
            lambda cur, _p: cur and (self.autosave.flush(), self.show_command(cur.data(Qt.ItemDataRole.UserRole))))
        sl.addWidget(self.cmd_list, 1)
        self.empty = EmptyArt("commands", "Пока пусто.\n\nОпишите справа словами, что должна\n"
                                          "делать команда, — ИИ соберёт шаги.")
        sl.addWidget(self.empty, 1)
        tip = QFrame()
        tip.setObjectName("card")
        tl = QHBoxLayout(tip)
        tl.setContentsMargins(12, 10, 12, 10)
        tl.addWidget(_small_icon("mic", 14), 0, Qt.AlignmentFlag.AlignTop)
        tl.addWidget(_label("Скажите «Джарвис» и фразу команды — всё выполнится само, мгновенно.", "hint"), 1)
        sl.addWidget(tip)
        row.addWidget(side)

        right = QVBoxLayout()
        right.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("canvas")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(30, 22, 30, 26)
        self.body.setSpacing(10)
        self.body.addWidget(self._ai_card())
        self.body.addSpacing(10)
        self.name = QLineEdit(placeholderText="Название команды")
        self.name.setObjectName("title")
        self.body.addWidget(self.name)

        self.body.addSpacing(6)
        self.body.addWidget(self._section("mic", "Как запустить",
                                          "Фраза после «Джарвис». {слово} — изменяемая часть: «найди на ютубе {запрос}»"))
        chips = QWidget()
        self.chips = FlowLayout(chips)
        self.body.addWidget(chips)
        self.phrase_in = QLineEdit(placeholderText="Добавить фразу — например «включи режим стрима» и Enter")
        self.phrase_in.returnPressed.connect(lambda: self.add_phrase(self.phrase_in.text()))
        self.phrase_in.editingFinished.connect(self._touch_now)
        self.body.addWidget(self.phrase_in)
        self._watch(self.name)

        self.body.addSpacing(12)
        self.body.addWidget(self._section("bolt", "Что сделать", "Шаги идут по порядку, сверху вниз"))
        steps = QWidget()
        self.steps_box = QVBoxLayout(steps)
        self.steps_box.setContentsMargins(0, 0, 0, 0)
        self.steps_box.setSpacing(8)
        self.body.addWidget(steps)
        add = QPushButton("  Добавить шаг")
        add.setObjectName("add")
        add.setIcon(qicon("plus", 14, C.TEXT_MED))
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(lambda: self._step_menu(add))
        self.body.addWidget(add)

        self.body.addSpacing(12)
        self.body.addWidget(self._section("timer", "Когда запускать сам",
                                          "необязательно: по расписанию или при открытии программы"))
        when = QWidget()
        self.when_box = QVBoxLayout(when)
        self.when_box.setContentsMargins(0, 0, 0, 0)
        self.when_box.setSpacing(8)
        self.body.addWidget(when)
        add_when = QPushButton("  Добавить условие")
        add_when.setObjectName("add")
        add_when.setIcon(qicon("plus", 14, C.TEXT_MED))
        add_when.setCursor(Qt.CursorShape.PointingHandCursor)
        add_when.clicked.connect(lambda: self._when_menu(add_when))
        self.body.addWidget(add_when)

        self.body.addSpacing(12)
        self.body.addWidget(self._section("check", "Настройки", ""))
        self.body.addWidget(self._options_card())
        self.body.addStretch(1)
        scroll.setWidget(body)
        right.addWidget(scroll, 1)
        right.addWidget(_line())
        right.addWidget(self._footer())
        row.addLayout(right, 1)
        return page

    @staticmethod
    def _section(icon: str, title: str, hint: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(_small_icon(icon, 13))
        lay.addWidget(_cap(title))
        if hint:
            lay.addWidget(_label("—  " + hint, "hint", wrap=False))
        lay.addStretch(1)
        return w

    def _ai_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("ai")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(IconBadge("spark", 30))
        t = QVBoxLayout()
        t.setSpacing(0)
        t.addWidget(_label("Опишите словами", "h2"))
        t.addWidget(_label("ИИ сам разложит на шаги — потом можно поправить руками", "hint"))
        head.addLayout(t, 1)
        lay.addLayout(head)
        line = QHBoxLayout()
        line.setSpacing(8)
        self.ai_text = QLineEdit(placeholderText="Открой OBS, подожди 2 секунды и включи музыку")
        self.ai_text.returnPressed.connect(self.ask_ai)
        self.ai_btn = QPushButton("  Собрать")
        self.ai_btn.setObjectName("primary")
        self.ai_btn.setIcon(qicon("spark", 14, C.BG))
        self.ai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_btn.setFixedWidth(128)
        self.ai_btn.clicked.connect(self.ask_ai)
        line.addWidget(self.ai_text, 1)
        line.addWidget(self.ai_btn)
        lay.addLayout(line)
        return card

    def _options_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 6, 16, 6)
        lay.setSpacing(0)

        def row(title: str, hint: str, toggle: Toggle, extra: QWidget | None = None, sep=True):
            w = QWidget()
            r = QHBoxLayout(w)
            r.setContentsMargins(0, 10, 0, 10)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(_label(title, "stepTitle"))
            col.addWidget(_label(hint, "hint"))
            r.addLayout(col, 1)
            if extra:
                r.addWidget(extra)
            r.addWidget(toggle)
            lay.addWidget(w)
            if sep:
                lay.addWidget(_line())

        self.only_app = Toggle()
        self.app = QLineEdit(placeholderText="chrome.exe")
        self.app.setFixedWidth(170)
        self.only_app.toggled.connect(self.app.setEnabled)
        row("Только в программе", "Работает, когда эта программа впереди", self.only_app, self.app)
        self.confirm = Toggle()
        row("Спрашивать перед запуском", "Для того, что жалко сделать случайно", self.confirm)
        self.enabled = Toggle()
        row("Команда включена", "Выключенную Джарвис пропускает", self.enabled, sep=False)
        self._watch(card)
        return card

    def _footer(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(30, 12, 30, 12)
        lay.setSpacing(10)
        self.status = _label("", "status")
        lay.addWidget(self.status, 1)
        self.del_btn = QPushButton("  Удалить")
        self.del_btn.setObjectName("danger")
        self.del_btn.setIcon(qicon("trash", 14, C.TEXT_DIM))
        self.del_btn.clicked.connect(self.delete_command)
        self.test_btn = QPushButton("  Проверить")
        self.test_btn.setIcon(qicon("play", 12, C.TEXT_MED))
        self.test_btn.clicked.connect(self.test_command)
        for b in (self.del_btn, self.test_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(b)
        return w

    # ── список ───────────────────────────────────────────────────────────────
    def reload(self):
        q = mc._norm(self.search.text())
        self.cmd_list.blockSignals(True)
        self.cmd_list.clear()
        own = [c for c in self.store.commands if not c.pack]
        for c in sorted(own, key=lambda c: c.name.lower()):
            phrase = c.phrases[0] if c.phrases else ""
            if q and q not in mc._norm(c.name + " " + phrase):
                continue
            n = len(c.steps)
            start = f"«{phrase}»" if phrase else (mt.describe_when(c.when[0]) if c.when else "—")
            if phrase and c.when:
                start += " · ⏰"
            item = QListWidgetItem(f"{c.name}\n{start} · {n} {plural(n)}")
            item.setData(Qt.ItemDataRole.UserRole, c.id)
            item.setIcon(qicon("bolt", 16, C.PRI if c.enabled else C.TEXT_DIM))
            if not c.enabled:
                item.setForeground(QColor(C.TEXT_DIM))
            self.cmd_list.addItem(item)
            if self.current and c.id == self.current.id:
                self.cmd_list.setCurrentItem(item)
        self.cmd_list.blockSignals(False)
        self.empty.setVisible(not own)
        self.cmd_list.setVisible(bool(own))

    # ── форма ────────────────────────────────────────────────────────────────
    def show_command(self, cid):
        c = next((x for x in self.store.commands if x.id == cid), None)
        self.autosave.cancel()
        self.current = c
        self._fill(c or mc.Command("", [], [], enabled=True))
        self.del_btn.setVisible(c is not None)
        self.status.setText("")

    def _fill(self, c: mc.Command):
        self._loading = True
        try:
            self._fill_form(c)
        finally:
            self._loading = False

    def _fill_form(self, c: mc.Command):
        self.name.setText(c.name)
        self._phrases = []
        for p in c.phrases:
            self.add_phrase(p)
        self._render_chips()
        self.only_app.setChecked(bool(c.app))
        self.app.setText(c.app)
        self.app.setEnabled(bool(c.app))
        self.confirm.setChecked(c.confirm)
        self.enabled.setChecked(c.enabled)
        for r in self.step_rows:
            r.setParent(None)
            r.deleteLater()
        self.step_rows = []
        for s in c.steps:
            self.add_step(s)
        for r in self.when_rows:
            r.setParent(None)
            r.deleteLater()
        self.when_rows = []
        for w in c.when:
            self.add_when(w)

    def new_command(self):
        self.autosave.flush()
        self.cmd_list.clearSelection()
        self.show_command(None)
        self.ai_text.setFocus()

    def add_phrase(self, text: str):
        text = " ".join((text or "").split()).strip(" «»\"")
        if text and text.lower() not in (p.lower() for p in self._phrases):
            self._phrases.append(text)
            self._render_chips()
            self._touch_now()
        self.phrase_in.clear()

    def _remove_phrase(self, text: str):
        self._phrases = [p for p in self._phrases if p != text]
        self._render_chips()

    def _render_chips(self):
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for p in self._phrases:
            chip = Chip(p)
            chip.removed.connect(self._remove_phrase)
            self.chips.addWidget(chip)
        self.chips.parentWidget().setVisible(bool(self._phrases))

    def phrases(self) -> list[str]:
        extra = "" if self._typing_phrase else " ".join(self.phrase_in.text().split())
        return self._phrases + ([extra] if extra and extra not in self._phrases else [])

    def _when_menu(self, anchor: QPushButton):
        menu = QMenu(self)
        for kind, (icon, title, _h) in WHEN_META.items():
            act = menu.addAction(qicon(icon, 16, C.PRI), "  " + title)
            act.triggered.connect(lambda _=False, k=kind: self.add_when({"on": k}))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def add_when(self, when: dict) -> WhenRow:
        row = WhenRow(when)
        row.removed.connect(self._remove_when)
        self.when_rows.append(row)
        self.when_box.addWidget(row)
        self._watch(row)
        self._touch_now()
        return row

    def _remove_when(self, row: WhenRow):
        self.when_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._touch_now()

    def _step_menu(self, anchor: QPushButton):
        menu = QMenu(self)
        for kind in STEP_ORDER:
            icon, title, _h = STEP_META[kind]
            act = menu.addAction(qicon(icon, 16, C.PRI), "  " + title)
            act.triggered.connect(lambda _=False, k=kind: self.add_step({"do": k, "value": ""}))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def add_step(self, step: dict) -> StepRow:
        row = StepRow(step)
        row.moved.connect(self._move_step)
        row.removed.connect(self._remove_step)
        self.step_rows.append(row)
        self.steps_box.addWidget(row)
        self._renumber()
        self._watch(row)
        self._touch_now()
        return row

    def _move_step(self, row: StepRow, d: int):
        i = self.step_rows.index(row)
        j = i + d
        if not 0 <= j < len(self.step_rows):
            return
        self.step_rows[i], self.step_rows[j] = self.step_rows[j], self.step_rows[i]
        for r in self.step_rows:
            self.steps_box.removeWidget(r)
        for r in self.step_rows:
            self.steps_box.addWidget(r)
        self._renumber()
        self._touch_now()

    def _remove_step(self, row: StepRow):
        self.step_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._renumber()
        self._touch_now()

    def _renumber(self):
        for i, r in enumerate(self.step_rows, 1):
            r.number, r.last = i, i == len(self.step_rows)
            r.rail.update()

    def read_steps(self) -> list[dict]:
        return mc.clean_steps([r.read() for r in self.step_rows])

    def form(self) -> mc.Command:
        cur = self.current
        return mc.Command(name=self.name.text().strip() or "Без названия", phrases=self.phrases(),
                          steps=self.read_steps(), app=self.app.text().strip() if self.only_app.isChecked() else "",
                          confirm=self.confirm.isChecked(), enabled=self.enabled.isChecked(),
                          pack=cur.pack if cur else "", id=cur.id if cur else mc.Command("", [], []).id,
                          when=mt.clean_when([r.read() for r in self.when_rows]))

    def _say(self, text: str, ok: bool = True):
        self.status.setStyleSheet(f"color: {C.PRI if ok else C.ACC};")
        self.status.setText(text)

    # ── автосохранение ───────────────────────────────────────────────────────
    def _watch(self, w: QWidget):
        """Любая правка внутри w — в автосохранение: текст — когда перестали
        печатать, списки и переключатели — сразу."""
        edits = [w] if isinstance(w, QLineEdit) else w.findChildren(QLineEdit)
        for e in edits:
            e.textChanged.connect(self._touched)
            e.editingFinished.connect(self.autosave.flush)
        for cb in w.findChildren(QComboBox):
            cb.currentIndexChanged.connect(self._touch_now)
        for b in w.findChildren(QPushButton) + w.findChildren(Toggle):
            if b.isCheckable():
                b.toggled.connect(self._touch_now)

    def _touched(self, *_):
        if not self._loading:
            self.autosave.touch()

    def _touch_now(self, *_):
        if not self._loading:
            self.autosave.touch()
            self.autosave.flush()

    def _blank(self) -> bool:
        return not (self.name.text().strip() or self._phrases or self.step_rows or self.when_rows)

    def _autosave(self):
        if self.current is None and self._blank():
            return                                   # пустая новая форма — сохранять нечего
        self._typing_phrase = self.phrase_in.hasFocus()
        try:
            if self.save_command():
                self.saved.show_note()
        finally:
            self._typing_phrase = False

    def hideEvent(self, ev):
        self.autosave.flush()                        # ушли с экрана — ничего не теряем
        super().hideEvent(ev)

    def save_command(self) -> bool:
        c = self.form()
        if len(c.when) < len(self.when_rows):
            self._say("Время — в виде 09:00, программа — её имя (obs, steam).", False)
            return False
        if any(r.kind == "time" and not any(b.isChecked() for b in r.days) for r in self.when_rows):
            self._say("Отметьте хотя бы один день недели.", False)
            return False
        if not c.phrases and not c.when:
            self._say("Добавьте фразу или условие «Когда запускать сам».", False)
            self.phrase_in.setFocus()
            return False
        if not c.steps:
            self._say("Добавьте хотя бы один шаг.", False)
            return False
        self.store.upsert(c)
        self.current = c
        if not self._typing_phrase:
            self.phrase_in.clear()
        self._phrases = list(c.phrases)
        self._render_chips()
        self.reload()
        self.del_btn.setVisible(True)
        how = [f"скажите «Джарвис, {c.phrases[0]}»"] if c.phrases else []
        how += [mt.describe_when(w) for w in c.when]
        self._say("Запуск: " + ", ".join(how) + ".")
        return True

    def delete_command(self):
        self.autosave.cancel()
        if self.current and self.store.delete(self.current.id):
            name = self.current.name
            self.new_command()
            self.reload()
            self._say(f"Команда «{name}» удалена.")

    def test_command(self):
        c = self.form()
        if not c.steps:
            self._say("Нечего проверять — добавьте шаги.", False)
            return
        self._say(self.store.run(c) + " Шаги идут в окне, которое сейчас впереди.")

    # ── ИИ ───────────────────────────────────────────────────────────────────
    def ask_ai(self):
        text = self.ai_text.text().strip()
        if not text:
            self._say("Напишите, что должна делать команда.", False)
            self.ai_text.setFocus()
            return
        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("  Собираю…")

        def work():
            try:
                self._ai_done.emit(self.build(text), "")
            except Exception as exc:
                logger.warning("Сборка команды ИИ: %s", exc)
                self._ai_done.emit(None, str(exc))
        threading.Thread(target=work, daemon=True, name="macro-ai").start()

    def _on_ai(self, data, err: str):
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("  Собрать")
        if not data:
            self._say(f"Не собралось: {err}", False)
            return
        cmd = mc.Command.from_dict(data)
        cmd.enabled = True
        self.cmd_list.clearSelection()
        self.current = None
        self.del_btn.setVisible(False)
        self._fill(cmd)
        if self.save_command():
            self.saved.show_note()
            self._say(f"Готово — {len(cmd.steps)} {plural(len(cmd.steps))}, команда сохранена. "
                      "Поправьте, если нужно, — изменения сохранятся сами.")

    # ── паки ─────────────────────────────────────────────────────────────────
    def _packs_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("canvas")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(30, 24, 30, 24)
        lay.setSpacing(16)
        lay.addWidget(_label("Готовые команды для программ", "h1"))
        lay.addWidget(_label("Ставятся в один клик и работают, только когда программа впереди: «новая вкладка» — "
                             "в браузере, «кисть» — в Photoshop.", "hint"))
        grid = QGridLayout()
        grid.setSpacing(14)
        self.pack_btns: dict[str, QPushButton] = {}
        for i, (key, p) in enumerate(PACKS.items()):
            grid.addWidget(self._pack_card(key, p), i // 2, i % 2)
        lay.addLayout(grid)
        lay.addStretch(1)
        scroll.setWidget(body)
        self._paint_packs()
        return scroll

    def _pack_card(self, key: str, p: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge(_PACK_ICON.get(key, "grid"), 40))
        t = QVBoxLayout()
        t.setSpacing(0)
        t.addWidget(_label(p["title"], "h2"))
        n = len(p["commands"])
        t.addWidget(_label(f"{n} {plural(n, ('команда', 'команды', 'команд'))}", "hint"))
        head.addLayout(t, 1)
        btn = QPushButton()
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(148)
        btn.clicked.connect(lambda _=False, k=key: self.toggle_pack(k))
        self.pack_btns[key] = btn
        head.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        lay.addWidget(_label(p["about"][:1].upper() + p["about"][1:], "hint"))
        ex = QWidget()
        flow = FlowLayout(ex, 6)
        for c in p["commands"][:4]:
            chip = QLabel(f"«{c['phrases'][0]}»")
            chip.setStyleSheet(f"color: {C.TEXT_MED}; background: {C.DARK}; border: 1px solid {C.BORDER};"
                               f" border-radius: 11px; padding: 3px 10px; font-size: 12px;")
            flow.addWidget(chip)
        lay.addWidget(ex)
        return card

    def _paint_packs(self):
        for key, btn in self.pack_btns.items():
            on = key in self.store.installed
            btn.setText("  Установлен" if on else "  Установить")
            btn.setIcon(qicon("check", 12, C.PRI) if on else qicon("plus", 12, C.BG))
            btn.setObjectName("" if on else "primary")
            btn.setToolTip("Нажмите, чтобы убрать пак" if on else "Поставить команды пака")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def toggle_pack(self, key: str):
        if key in self.store.installed:
            self.store.remove_pack(key)
        else:
            self.store.install_pack(key)
        self._paint_packs()


_PACK_ICON = {"windows": "app", "browser": "globe", "telegram": "plane", "vscode": "keyboard",
              "photoshop": "cursor", "discord": "mic", "spotify": "note"}


def open_dialog(parent=None) -> MacrosDialog:
    dlg = MacrosDialog(parent)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
