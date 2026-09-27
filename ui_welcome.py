"""Окно «Добро пожаловать» — оно же «Что умеет Джарвис».

Слева три раздела: Первые шаги (чек-лист настройки с живыми галочками и
кнопкой к каждому шагу), Как говорить (имя, капсула, цвета, клавиши),
Что умеет (примеры по разделам — нажмите, и Джарвис выполнит). При первом
запуске открывается само; дальше — трей и «что ты умеешь».
Содержание — core/help.py; вид — общий (ui_kit).
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QScrollArea,
                             QStackedWidget, QVBoxLayout, QWidget)

from core import help as H
from ui import C
from ui_icons import qicon
from ui_kit import STYLE, FlowLayout, IconBadge, Progress, _label, _line

logger = logging.getLogger(__name__)

EXTRA = f"""
QPushButton#nav {{ border: none; border-radius: 10px; padding: 10px 14px; text-align: left; color: {C.TEXT_MED}; }}
QPushButton#nav:hover {{ background: {C.PANEL2}; color: {C.WHITE}; }}
QPushButton#nav:checked {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
QPushButton#phrase {{ background: {C.DARK}; border: 1px solid {C.BORDER}; border-radius: 13px; padding: 5px 12px;
  color: {C.TEXT}; font-size: 12px; }}
QPushButton#phrase:hover {{ border-color: {C.PRI_DIM}; color: {C.PRI}; background: {C.PRI_GHO}; }}
QLabel#subj {{ color: {C.WHITE}; font-weight: 600; }}
QLabel#done {{ color: {C.PRI}; font-size: 12px; }}
QLabel#todo {{ color: {C.TEXT_DIM}; font-size: 12px; }}
QLabel#num {{ color: {C.PRI}; font-family: Consolas; font-weight: bold; border: 1.5px solid {C.PRI_DIM};
  border-radius: 12px; min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }}
QLabel#numdone {{ color: {C.BG}; background: {C.PRI}; border-radius: 12px; font-weight: bold;
  min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; }}
QLabel#opt {{ color: {C.TEXT_DIM}; border: 1px solid {C.BORDER_B}; border-radius: 7px; padding: 0 6px;
  font-size: 10px; }}
"""


class WelcomeDialog(QDialog):
    def __init__(self, parent=None, actions: dict | None = None, try_phrase=None, first_run: bool = False):
        super().__init__(parent)
        self.actions = actions or {}
        self.try_phrase = try_phrase
        self.setWindowTitle("ДЖАРВИС — что я умею")
        self.setStyleSheet(STYLE + EXTRA)
        self.resize(1000, 700)
        self.setMinimumSize(860, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header(first_run))
        root.addWidget(_line())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._sidebar())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._steps_page())
        self.pages.addWidget(self._howto_page())
        self.pages.addWidget(self._abilities_page())
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        self.refresh()
        self.show_page(0)

    # ── каркас ───────────────────────────────────────────────────────────────
    def _header(self, first_run: bool) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("spark", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label("ДЖАРВИС", "brand"))
        col.addWidget(_label("Добро пожаловать" if first_run else "Что умеет Джарвис", "h1", wrap=False))
        lay.addLayout(col)
        lay.addStretch(1)
        done = QPushButton("Понятно")
        done.setObjectName("primary")
        done.setFixedWidth(130)
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.clicked.connect(self.close)
        lay.addWidget(done)
        return w

    def _sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(250)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 14, 16)
        sl.setSpacing(4)
        self.navs = []
        for i, (icon, title) in enumerate((("check", "Первые шаги"), ("mic", "Как говорить"), ("grid", "Что умеет"))):
            b = QPushButton("   " + title)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(qicon(icon, 15, C.PRI))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=i: self.show_page(k))
            sl.addWidget(b)
            self.navs.append(b)
        sl.addStretch(1)
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 12)
        cl.setSpacing(6)
        self.side_progress_label = _label("", "stepTitle")
        cl.addWidget(self.side_progress_label)
        self.side_progress = Progress()
        cl.addWidget(self.side_progress)
        cl.addWidget(_label("Необязательное можно сделать потом — Джарвис работает и так.", "hint"))
        sl.addWidget(card)
        return side

    def show_page(self, i: int):
        self.pages.setCurrentIndex(i)
        for k, b in enumerate(self.navs):
            b.setChecked(k == i)

    @staticmethod
    def _scroll(inner: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(inner)
        return sc

    # ── первые шаги ──────────────────────────────────────────────────────────
    def _steps_page(self) -> QWidget:
        inner = QWidget()
        inner.setObjectName("canvas")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)
        hello = QFrame()
        hello.setObjectName("ai")
        hl = QHBoxLayout(hello)
        hl.setContentsMargins(18, 14, 18, 14)
        hl.setSpacing(14)
        hl.addWidget(IconBadge("mic", 36))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_label("Привет! Я Джарвис — ваш голосовой помощник на этом компьютере.", "h2"))
        col.addWidget(_label("Просто скажите «Джарвис» и что нужно: «Джарвис, включи музыку». "
                             "Ниже — что стоит настроить, чтобы я помогал лучше. Обязательный шаг один.", "hint"))
        hl.addLayout(col, 1)
        lay.addWidget(hello)
        self.steps_card = QFrame()
        self.steps_card.setObjectName("card")
        self.steps_box = QVBoxLayout(self.steps_card)
        self.steps_box.setContentsMargins(16, 4, 16, 4)
        self.steps_box.setSpacing(0)
        lay.addWidget(self.steps_card)
        lay.addStretch(1)
        return self._scroll(inner)

    def refresh(self):
        """Галочки — заново (после того как человек что-то настроил)."""
        while self.steps_box.count():
            it = self.steps_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        items = H.checklist()
        for i, (step, done, detail) in enumerate(items, 1):
            row = QWidget()
            r = QHBoxLayout(row)
            r.setContentsMargins(0, 12, 0, 12)
            r.setSpacing(14)
            num = _label("✓" if done else str(i), "numdone" if done else "num", wrap=False)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            r.addWidget(num)
            col = QVBoxLayout()
            col.setSpacing(1)
            top = QHBoxLayout()
            top.setSpacing(8)
            top.addWidget(_label(step.title, "subj" if not done else "stepTitle", wrap=False))
            if step.optional:
                top.addWidget(_label("по желанию", "opt", wrap=False))
            top.addStretch(1)
            col.addLayout(top)
            col.addWidget(_label(step.why, "hint"))
            if detail:
                col.addWidget(_label(("●  " if done else "○  ") + detail, "done" if done else "todo"))
            r.addLayout(col, 1)
            btn = QPushButton("Изменить" if done else "Настроить")
            btn.setObjectName("" if done else ("primary" if not step.optional else ""))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, a=step.action: self.run_action(a))
            r.addWidget(btn)
            self.steps_box.addWidget(row)
            if i < len(items):
                self.steps_box.addWidget(_line())
        done_n = sum(1 for _s, d, _t in items if d)
        self.side_progress_label.setText(f"Настроено {done_n} из {len(items)}")
        self.side_progress.value = done_n / len(items)
        self.side_progress.update()

    def run_action(self, action: str):
        fn = self.actions.get(action)
        if fn:
            fn()
        else:
            logger.info("Подсказки: действие %s недоступно", action)

    def event(self, ev):
        # Вернулись в окно после настройки — галочки обновить.
        if ev.type() == QEvent.Type.WindowActivate and hasattr(self, "steps_box"):
            self.refresh()
        return super().event(ev)

    # ── как говорить ─────────────────────────────────────────────────────────
    def _howto_page(self) -> QWidget:
        inner = QWidget()
        inner.setObjectName("canvas")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)
        lay.addWidget(_label("Как со мной говорить", "h1", wrap=False))
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, (icon, title, text) in enumerate(H.HOW_TO):
            card = QFrame()
            card.setObjectName("card")
            cl = QHBoxLayout(card)
            cl.setContentsMargins(16, 14, 16, 14)
            cl.setSpacing(12)
            cl.addWidget(IconBadge(icon, 34), 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(3)
            col.addWidget(_label(title, "h2"))
            col.addWidget(_label(text, "hint"))
            cl.addLayout(col, 1)
            grid.addWidget(card, i // 2, i % 2)
        lay.addLayout(grid)
        lay.addStretch(1)
        return self._scroll(inner)

    # ── что умеет ────────────────────────────────────────────────────────────
    def _abilities_page(self) -> QWidget:
        inner = QWidget()
        inner.setObjectName("canvas")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(_label("Что я умею", "h1", wrap=False))
        top.addStretch(1)
        self.search = QLineEdit(placeholderText="Найти — «таймер», «музыка», «маме»")
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search)
        lay.addLayout(top)
        self.try_hint = _label("Нажмите на фразу — я выполню её, как будто вы сказали. Говорить её можно и так: "
                               "«Джарвис, …».", "hint")
        lay.addWidget(self.try_hint)
        self.ability_cards: list[tuple[QFrame, str]] = []
        for title, icon, phrases in H.ABILITIES:
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 12, 16, 14)
            cl.setSpacing(8)
            head = QHBoxLayout()
            head.setSpacing(10)
            head.addWidget(IconBadge(icon, 28))
            head.addWidget(_label(title, "h2", wrap=False))
            head.addStretch(1)
            cl.addLayout(head)
            wrap = QWidget()
            flow = FlowLayout(wrap, 6)
            for ph in phrases:
                b = QPushButton(f"«{ph}»")
                b.setObjectName("phrase")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, text=ph: self.try_it(text))
                flow.addWidget(b)
            cl.addWidget(wrap)
            lay.addWidget(card)
            self.ability_cards.append((card, (title + " " + " ".join(phrases)).lower()))
        self.nothing = _label("Не нашёл — просто спросите Джарвиса своими словами.", "hint")
        self.nothing.hide()
        lay.addWidget(self.nothing)
        lay.addStretch(1)
        return self._scroll(inner)

    def _filter(self, text: str):
        q = text.strip().lower()
        shown = 0
        for card, hay in self.ability_cards:
            vis = not q or q in hay
            card.setVisible(vis)
            shown += vis
        self.nothing.setVisible(shown == 0)

    def try_it(self, text: str):
        if self.try_phrase:
            self.try_phrase(text)
            self.try_hint.setText(f"✓  Отправил Джарвису: «{text}»")
        else:
            QGuiApplication.clipboard().setText(text)
            self.try_hint.setText(f"Скопировал «{text}» — скажите это Джарвису.")
        self.try_hint.setStyleSheet(f"color: {C.PRI};")

    def closeEvent(self, ev):
        H.mark_seen()
        super().closeEvent(ev)


def open_dialog(parent=None, actions=None, try_phrase=None, first_run: bool = False) -> WelcomeDialog:
    dlg = WelcomeDialog(parent, actions, try_phrase, first_run)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
