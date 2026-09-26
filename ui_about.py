"""Окно «Обо мне».

Сверху — сколько Джарвис о вас знает и кнопка «Познакомиться голосом»
(он сам спросит по одному вопросу). Ниже — анкета по разделам: у каждого
вопроса видно, ЗАЧЕМ он Джарвису; ответ сохраняется сам, как только вы
закончили печатать. В конце — что Джарвис запомнил сам из разговоров, с
возможностью удалить любой факт. Логика — core/about_me.py.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from core import about_me as AB
from ui import C
from ui_icons import qicon
from ui_kit import STYLE, IconBadge, Progress, _cap, _icon_btn, _label, _line, _small_icon

logger = logging.getLogger(__name__)

GROUP_ICON = {"Кто вы": "person", "Распорядок дня": "timer", "Что вам нравится": "note",
              "Люди и цели": "spark", "Здоровье": "lock"}


class AboutDialog(QDialog):
    def __init__(self, parent=None, start_voice=None):
        super().__init__(parent)
        self.start_voice = start_voice
        self.setWindowTitle("ДЖАРВИС — обо мне")
        self.setStyleSheet(STYLE)
        self.resize(880, 760)
        self.setMinimumSize(720, 560)
        self.edits: dict[str, QLineEdit] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(_line())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName("canvas")
        outer = QHBoxLayout(canvas)
        outer.setContentsMargins(0, 0, 0, 0)
        col_w = QWidget()
        col_w.setMaximumWidth(820)
        self.col = QVBoxLayout(col_w)
        self.col.setContentsMargins(28, 22, 28, 28)
        self.col.setSpacing(12)
        self.col.addWidget(self._summary())
        have = AB.answers()
        for group in AB.GROUPS:
            qs = [q for q in AB.QUESTIONS if q.group == group]
            self.col.addSpacing(6)
            self.col.addWidget(self._section(GROUP_ICON.get(group, "person"), group,
                                             "по желанию" if all(q.sensitive for q in qs) else ""))
            self.col.addWidget(self._group_card(qs, have))
        self.col.addSpacing(10)
        self.col.addWidget(self._section("spark", "Джарвис запомнил сам", "из разговоров — лишнее можно удалить"))
        self.facts_box = QVBoxLayout()
        self.facts_box.setSpacing(0)
        self.facts_card = QFrame()
        self.facts_card.setObjectName("card")
        fl = QVBoxLayout(self.facts_card)
        fl.setContentsMargins(16, 6, 16, 6)
        fl.addLayout(self.facts_box)
        self.col.addWidget(self.facts_card)
        self.col.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(col_w, 100)
        outer.addStretch(1)
        scroll.setWidget(canvas)
        root.addWidget(scroll, 1)
        root.addWidget(_line())
        root.addWidget(self._footer())
        self.render_facts()
        self.update_summary()

    # ── шапка и сводка ───────────────────────────────────────────────────────
    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("person", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label("ДЖАРВИС", "brand"))
        col.addWidget(_label("Обо мне", "h1", wrap=False))
        lay.addLayout(col)
        lay.addStretch(1)
        self.voice_btn = QPushButton("  Познакомиться голосом")
        self.voice_btn.setObjectName("primary")
        self.voice_btn.setIcon(qicon("mic", 14, C.BG))
        self.voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_btn.clicked.connect(self._voice)
        self.voice_btn.setVisible(self.start_voice is not None)
        lay.addWidget(self.voice_btn)
        return w

    def _summary(self) -> QWidget:
        card = QFrame()
        card.setObjectName("ai")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(IconBadge("spark", 32))
        t = QVBoxLayout()
        t.setSpacing(1)
        self.summary = _label("", "h2")
        t.addWidget(self.summary)
        t.addWidget(_label("Чем больше Джарвис знает, тем лучше помогает: будит к вашему подъёму, говорит "
                           "вашим тоном, понимает «напиши маме». Всё хранится только у вас.", "hint"))
        top.addLayout(t, 1)
        lay.addLayout(top)
        self.progress = Progress()
        lay.addWidget(self.progress)
        return card

    def update_summary(self):
        known, total = AB.progress()
        self.summary.setText(f"Джарвис знает о вас {known} из {total}")
        self.progress.value = known / total if total else 0
        self.progress.update()

    @staticmethod
    def _section(icon: str, title: str, hint: str = "") -> QWidget:
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

    # ── анкета ───────────────────────────────────────────────────────────────
    def _group_card(self, qs, have: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 4, 16, 4)
        lay.setSpacing(0)
        for i, q in enumerate(qs):
            row = QWidget()
            r = QHBoxLayout(row)
            r.setContentsMargins(0, 10, 0, 10)
            r.setSpacing(14)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(_label(q.label, "stepTitle"))
            col.addWidget(_label(q.why, "hint"))
            box = QWidget()
            box.setFixedWidth(250)
            box.setLayout(col)
            r.addWidget(box)
            e = QLineEdit(have.get(q.key, ""))
            e.setPlaceholderText(q.placeholder if q.sensitive else f"например: {q.placeholder}")
            e.editingFinished.connect(lambda k=q.key, w=e: self.save(k, w.text()))
            self.edits[q.key] = e
            r.addWidget(e, 1)
            clear = _icon_btn("close", "Забыть", 11, C.TEXT_DIM)
            clear.clicked.connect(lambda _=False, k=q.key, w=e: (w.clear(), self.save(k, "")))
            r.addWidget(clear)
            lay.addWidget(row)
            if i < len(qs) - 1:
                lay.addWidget(_line())
        return card

    def save(self, key: str, text: str):
        before = AB.answers().get(key, "")
        text = " ".join((text or "").split())
        if text == before:
            return
        AB.answer(key, text) if text else AB.forget(key)
        self._say(f"✓  {AB.BY_KEY[key].label}: " + (f"запомнил «{text}»" if text else "забыл"))
        self.update_summary()

    # ── запомнил сам ─────────────────────────────────────────────────────────
    def render_facts(self):
        while self.facts_box.count():
            it = self.facts_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        facts = AB.other_facts()
        if not facts:
            self.facts_box.addWidget(_label("Пока ничего — Джарвис запоминает важное из разговоров сам.", "hint"))
            return
        for i, (cat, key, value) in enumerate(facts):
            row = QWidget()
            r = QHBoxLayout(row)
            r.setContentsMargins(0, 8, 0, 8)
            r.setSpacing(12)
            tag = _label(AB.category_title(cat).upper(), "cap", wrap=False)
            tag.setFixedWidth(150)
            r.addWidget(tag)
            r.addWidget(_label(f"{key.replace('_', ' ')}: {value}", "", wrap=True), 1)
            rm = _icon_btn("trash", "Забыть этот факт", 13, C.TEXT_DIM)
            rm.clicked.connect(lambda _=False, c=cat, k=key: self.forget_fact(c, k))
            r.addWidget(rm)
            self.facts_box.addWidget(row)
            if i < len(facts) - 1:
                self.facts_box.addWidget(_line())

    def forget_fact(self, cat: str, key: str):
        from memory.memory_manager import forget
        forget(cat, key)
        self.render_facts()
        self._say(f"Забыл «{key.replace('_', ' ')}».")

    # ── низ ──────────────────────────────────────────────────────────────────
    def _footer(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(28, 12, 28, 12)
        self.status = _label("Ответы сохраняются сами. Пустое поле — Джарвис забудет.", "status")
        lay.addWidget(self.status, 1)
        done = QPushButton("Готово")
        done.setObjectName("primary")
        done.setFixedWidth(120)
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.clicked.connect(self.close)
        lay.addWidget(done)
        return w

    def _say(self, text: str):
        self.status.setStyleSheet(f"color: {C.PRI};")
        self.status.setText(text)

    def _voice(self):
        for key, e in self.edits.items():                   # несохранённое — сначала сохранить
            self.save(key, e.text())
        if self.start_voice:
            self.start_voice()
            self._say("Джарвис начинает знакомство — отвечайте голосом.")

    def closeEvent(self, ev):
        for key, e in self.edits.items():
            self.save(key, e.text())
        super().closeEvent(ev)


def open_dialog(parent=None, start_voice=None) -> AboutDialog:
    dlg = AboutDialog(parent, start_voice=start_voice)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
