"""Окно «Обо мне».

Сверху — сколько Джарвис о вас знает и кнопка «Познакомиться голосом»
(он сам спросит по одному вопросу). Ниже — анкета по разделам: у каждого
вопроса видно, ЗАЧЕМ он Джарвису; ответ сохраняется сам, как только вы
закончили печатать. В конце — что Джарвис запомнил сам из разговоров, с
возможностью удалить любой факт. Логика — core/about_me.py.
"""
from __future__ import annotations

import logging

import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from core import about_me as AB
from ui import C
from ui_icons import qicon
from memory.memory_manager import fact_line as _fact_line
from ui_kit import STYLE, IconBadge, Progress, _cap, _icon_btn, _label, _line, _small_icon

logger = logging.getLogger(__name__)

GROUP_ICON = {"Кто вы": "person", "Распорядок дня": "timer", "Что вам нравится": "note",
              "Люди и цели": "spark", "Здоровье": "lock"}


def _record(sec: float) -> bytes:
    import sounddevice as sd
    data = sd.rec(int(sec * 16000), samplerate=16000, channels=1, dtype="int16")
    sd.wait()
    return data.tobytes()


class VoiceEnrollDialog(QDialog):
    """Запись голоса: 5 фраз по 4 секунды — читать вслух то, что на экране."""
    _done = pyqtSignal(dict)

    def __init__(self, parent=None, vid=None, record=None):
        super().__init__(parent)
        from core import voice_id as V
        self.V = V
        self.vid = vid or V.voice_id()
        self.record = record or _record
        self.takes: list[bytes] = []
        self.setWindowTitle("ДЖАРВИС — ваш голос")
        self.setStyleSheet(STYLE)
        self.resize(560, 330)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(IconBadge("mic", 34))
        self.step = _label("", "h2")
        head.addWidget(self.step, 1)
        lay.addLayout(head)
        lay.addWidget(_label("Прочитайте вслух фразу ниже обычным голосом, как говорите с Джарвисом. "
                             "Запись — 4 секунды.", "hint"))
        self.phrase = _label("", "h1")
        self.phrase.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.phrase.setMinimumHeight(90)
        lay.addWidget(self.phrase)
        self.bar = Progress()
        lay.addWidget(self.bar)
        self.status = _label("", "status")
        lay.addWidget(self.status)
        self.btn = QPushButton("  Записать")
        self.btn.setObjectName("primary")
        self.btn.setIcon(qicon("mic", 14, C.BG))
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self.take)
        lay.addWidget(self.btn)
        self._done.connect(self._finished)
        self._show_step()

    def _show_step(self):
        i = len(self.takes)
        n = len(self.V.ENROLL_PHRASES)
        self.step.setText(f"Фраза {min(i + 1, n)} из {n}")
        self.phrase.setText(f"«{self.V.ENROLL_PHRASES[min(i, n - 1)]}»")
        self.bar.value = i / n
        self.bar.update()

    def take(self):
        self.btn.setEnabled(False)
        self.btn.setText("  Говорите…")
        self.status.setText("")

        def work():
            try:
                pcm = self.record(self.V.ENROLL_SEC)
            except Exception as exc:
                self._done.emit({"error": f"Микрофон не записал: {exc}"})
                return
            self._done.emit({"take": pcm})
        threading.Thread(target=work, daemon=True, name="voice-take").start()

    def _finished(self, r: dict):
        if "error" in r:
            self.status.setText(r["error"])
            self.btn.setEnabled(True)
            self.btn.setText("  Записать")
            return
        if "take" in r:
            self.takes.append(r["take"])
            if len(self.takes) < len(self.V.ENROLL_PHRASES):
                self._show_step()
                self.btn.setEnabled(True)
                self.btn.setText("  Записать")
                return
            self.phrase.setText("Считаю отпечаток голоса…")
            self.bar.value = 1.0
            self.bar.update()
            threading.Thread(target=lambda: self._done.emit({"result": self.vid.enroll(self.takes)}),
                             daemon=True, name="voice-enroll").start()
            return
        res = r["result"]
        self.phrase.setText("✓  Готово" if res["ok"] else "Не получилось")
        self.status.setText(res["text"])
        self.btn.setEnabled(True)
        if res["ok"]:
            self.btn.setText("Закрыть")
            self.btn.clicked.disconnect()
            self.btn.clicked.connect(self.accept)
        else:
            self.takes = []
            self.btn.setText("  Записать заново")
            self._show_step()


class AboutDialog(QDialog):
    _voice_sig = pyqtSignal(str)

    def __init__(self, parent=None, start_voice=None, vid=None):
        super().__init__(parent)
        self.start_voice = start_voice
        from core import voice_id as V
        self.V = V
        self.vid = vid or V.voice_id()
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
        self.col.addSpacing(6)
        self.col.addWidget(self._section("mic", "Ваш голос", "опасное — только по вашему «да»"))
        self.col.addWidget(self._voice_card())
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
        col.addWidget(_label("Джарвис", "brand"))
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

    # ── ваш голос ────────────────────────────────────────────────────────────
    def _voice_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("lock", 34))
        col = QVBoxLayout()
        col.setSpacing(1)
        self.voice_title = _label("", "stepTitle")
        col.addWidget(self.voice_title)
        col.addWidget(_label("Сообщения и звонки людям, выключение ПК, удаление файлов — только если «да» "
                             "сказали вы. Набранное с клавиатуры — всегда ваше.", "hint"))
        lay.addLayout(col, 1)
        self.voice_btn2 = QPushButton()
        self.voice_btn2.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_btn2.clicked.connect(self.voice_action)
        lay.addWidget(self.voice_btn2)
        self.voice_reset = _icon_btn("trash", "Удалить отпечаток голоса", 13, C.TEXT_DIM)
        self.voice_reset.clicked.connect(self.voice_forget)
        lay.addWidget(self.voice_reset)
        self._voice_sig.connect(self._voice_msg)
        self.paint_voice()
        return card

    def paint_voice(self):
        on, have_model = self.vid.enrolled(), self.vid.available()
        size = getattr(self.vid.embed, "size_mb", 26)
        self.voice_title.setText("●  Голос записан — проверка включена" if on else
                                 ("●  Модель голоса обновилась — перезапишите голос" if self.vid.needs_reenroll()
                                  else "●  Голос не записан") if have_model else f"●  Нет модели голоса ({size} МБ)")
        self.voice_title.setStyleSheet(f"color: {C.PRI if on else C.ACC2};")
        self.voice_btn2.setText("Перезаписать" if on else ("Записать голос" if have_model else "Скачать модель"))
        self.voice_btn2.setObjectName("" if on else "primary")
        self.voice_btn2.style().unpolish(self.voice_btn2)
        self.voice_btn2.style().polish(self.voice_btn2)
        self.voice_reset.setVisible(on)

    def voice_action(self):
        if not self.vid.available():
            self.voice_btn2.setEnabled(False)
            self._say(f"Скачиваю модель голоса ({getattr(self.vid.embed, 'size_mb', 26)} МБ)…")

            def work():
                try:
                    self.vid.download()
                    self._voice_sig.emit("✓  Модель скачана — запишите голос.")
                except Exception as exc:
                    self._voice_sig.emit(f"Не скачалась: {exc}")
            threading.Thread(target=work, daemon=True, name="spk-download").start()
            return
        dlg = VoiceEnrollDialog(self, self.vid)
        dlg.exec()
        self.paint_voice()

    def _voice_msg(self, text: str):
        self.voice_btn2.setEnabled(True)
        self._say(text)
        self.paint_voice()

    def voice_forget(self):
        self.vid.reset()
        self.paint_voice()
        self._say("Отпечаток голоса удалён — проверка выключена.")

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
            r.addWidget(_label(_fact_line(key.replace('_', ' '), value), "", wrap=True), 1)
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
