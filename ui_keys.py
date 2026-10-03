"""Окно «Ключи и подключения».

Каждый сервис — карточка: что он даёт, статус (работает / не задан /
ошибка — после НАСТОЯЩЕЙ проверки), поля с «глазом», «Где взять» по
шагам. Ключ сохраняется сам, как только вы его вставили; полный набор
полей сразу проверяется (кнопка «Проверить» — проверить ещё раз). Вход в Spotify и в аккаунт для
звонков — кнопкой прямо в карточке, без командной строки.

Логика и проверки — core/keys.py; вид — общий с окном команд (ui_kit).
"""
from __future__ import annotations

import logging
import threading
import webbrowser

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton,
                             QScrollArea,
                             QVBoxLayout, QWidget)

from core import keys as K
from ui import C
from ui_icons import qicon
from ui_kit import STYLE, Autosave, IconBadge, Progress, SavedNote, _cap, _icon_btn, _label, _line, _small_icon

logger = logging.getLogger(__name__)

# состояние → (подпись, цвет текста, фон)
PILL = {
    "ok": ("Работает", C.PRI, C.PRI_GHO),
    "warn": ("Почти готово", C.ACC2, "#15160a"),
    "bad": ("Ошибка", C.RED, "#1a0a0e"),
    "missing": ("Не задан", C.TEXT_DIM, C.DARK),
    "set": ("Не проверен", C.TEXT_MED, C.DARK),
    "busy": ("Проверяю…", C.TEXT_MED, C.DARK),
}

EXTRA_STYLE = f"""
QLabel#pill {{ border-radius: 9px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}
QLabel#req {{ color: {C.ACC}; border: 1px solid {C.ACC}; border-radius: 8px; padding: 1px 6px;
  font-family: Consolas; font-size: 9px; font-weight: bold; }}
QFrame#howto {{ background: {C.DARK}; border: 1px solid {C.BORDER}; border-radius: 10px; }}
QLabel#num {{ color: {C.PRI}; font-family: Consolas; font-weight: bold; }}
QLabel#msg {{ font-size: 12px; }}
QFrame#head {{ background: transparent; border: none; }}
"""


class ServiceCard(QFrame):
    checked = pyqtSignal(str, str, str)          # id, состояние, текст (из потока проверки)
    changed = pyqtSignal()
    saved = pyqtSignal()                         # ключ записан на диск (плашка «✓ Сохранено»)

    def __init__(self, service: K.Service, values: dict, parent=None):
        super().__init__(parent)
        self.s = service
        self.state = K.status(service, values)
        self.autosave = Autosave(self, self._autosave)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)

        # шапка: иконка, название, что даёт, статус, стрелка
        head = QFrame()
        head.setObjectName("head")
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(14)
        hl.addWidget(IconBadge(service.icon, 40))
        col = QVBoxLayout()
        col.setSpacing(2)
        tl = QHBoxLayout()
        tl.setSpacing(8)
        tl.addWidget(_label(service.title, "h2", wrap=False))
        if service.required:
            tl.addWidget(_label("ОБЯЗАТЕЛЬНО", "req", wrap=False))
        tl.addStretch(1)
        col.addLayout(tl)
        col.addWidget(_label(service.gives, "hint"))
        hl.addLayout(col, 1)
        self.pill = _label("", "pill", wrap=False)
        hl.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self.chev = _icon_btn("down", "Открыть", 14)
        self.chev.clicked.connect(self.toggle)
        hl.addWidget(self.chev)
        head.mousePressEvent = lambda _e: self.toggle()
        lay.addWidget(head)

        # тело: поля, где взять, кнопки
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 4, 0, 0)
        bl.setSpacing(10)
        bl.addWidget(_line())
        self.edits: dict[str, QLineEdit] = {}
        self.chips: dict[str, QLabel] = {}         # статус у поля: «Нет ключа» → «✓ Работает»
        self.notes: dict[str, QLabel] = {}         # под полем: «AIza••••Q7xF» или «не тот ключ»
        for f in service.fields:
            bl.addWidget(_cap(f.label + ("  · необязательно" if f.optional else "")))
            row = QHBoxLayout()
            row.setSpacing(6)
            e = QLineEdit(values.get(f.key, ""))
            e.setPlaceholderText(f.placeholder or "Вставьте сюда")
            if f.secret:
                e.setEchoMode(QLineEdit.EchoMode.Password)
            if K.from_env(f):
                e.setEnabled(False)
                e.setToolTip(f"Задан переменной среды {f.env} — меняется там")
            e.textChanged.connect(lambda _t: self._dirty())
            e.editingFinished.connect(lambda: self.autosave.flush())
            self.edits[f.key] = e
            row.addWidget(e, 1)
            if f.secret:
                eye = _icon_btn("eye", "Показать / скрыть")
                eye.clicked.connect(lambda _=False, w=e: (w.setEchoMode(
                    QLineEdit.EchoMode.Normal if w.echoMode() == QLineEdit.EchoMode.Password
                    else QLineEdit.EchoMode.Password), self._paint_fields()))
                row.addWidget(eye)
            paste = _icon_btn("copy", "Вставить из буфера")
            paste.clicked.connect(lambda _=False, w=e: w.setText(QGuiApplication.clipboard().text().strip()))
            row.addWidget(paste)
            chip = _label("", "fchip", wrap=False)
            chip.setMinimumWidth(96)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.chips[f.key] = chip
            row.addWidget(chip)
            bl.addLayout(row)
            note = _label("", "hint", wrap=True)
            note.hide()
            self.notes[f.key] = note
            bl.addWidget(note)

        how = QFrame()
        how.setObjectName("howto")
        hw = QVBoxLayout(how)
        hw.setContentsMargins(14, 12, 14, 12)
        hw.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(_small_icon("key", 13))
        top.addWidget(_cap("Где взять"))
        top.addStretch(1)
        if service.url:
            site = QPushButton("  Открыть сайт")
            site.setIcon(qicon("globe", 13, C.TEXT_MED))
            site.setCursor(Qt.CursorShape.PointingHandCursor)
            site.clicked.connect(lambda: webbrowser.open(service.url))
            top.addWidget(site)
        hw.addLayout(top)
        for i, step in enumerate(service.steps, 1):
            r = QHBoxLayout()
            r.setSpacing(8)
            n = _label(str(i), "num", wrap=False)
            n.setFixedWidth(14)
            r.addWidget(n, 0, Qt.AlignmentFlag.AlignTop)
            r.addWidget(_label(step, "hint"), 1)
            hw.addLayout(r)
        bl.addWidget(how)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.msg = _label("", "msg")
        foot.addWidget(self.msg, 1)
        self.action_btn = None
        if service.action:
            self.action_btn = QPushButton("  Войти в Spotify" if service.action == "spotify" else "  Войти по QR")
            self.action_btn.setIcon(qicon("lock", 13, C.TEXT_MED))
            self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action_btn.clicked.connect(self.run_action)
            foot.addWidget(self.action_btn)
        self.save_btn = QPushButton("Проверить")
        self.save_btn.setObjectName("primary")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self.save_and_check)
        foot.addWidget(self.save_btn)
        bl.addLayout(foot)
        lay.addWidget(self.body)

        self.checked.connect(self._on_checked)
        self.body.setVisible(self.state in ("missing", "partial") and service.required)
        self._paint()

    # ── вид ─────────────────────────────────────────────────────────────────
    def toggle(self, open_: bool | None = None):
        show = (not self.body.isVisible()) if open_ is None else open_
        self.body.setVisible(show)
        self.chev.setIcon(qicon("up" if show else "down", 14, C.TEXT_MED))

    def _paint(self):
        text, fg, bg = PILL.get(self.state, PILL["set"])
        if self.state == "partial":
            text, fg, bg = "Не хватает поля", C.ACC2, "#15160a"
        self.pill.setText("●  " + text)
        c = QColor(fg)
        self.pill.setStyleSheet(f"color: {fg}; background: {bg}; border-radius: 9px; padding: 2px 10px;"
                                f" border: 1px solid rgba({c.red()}, {c.green()}, {c.blue()}, 90);")
        border = {"ok": C.BORDER, "bad": "#3a1a22", "warn": "#2c2c14"}.get(self.state, C.BORDER)
        self.setStyleSheet(f"QFrame#card {{ border-color: {border}; }}")
        if hasattr(self, "chips"):
            self._paint_fields()

    def _say(self, text: str, state: str):
        color = {"ok": C.PRI, "warn": C.ACC2, "bad": C.RED}.get(state, C.TEXT_MED)
        self.msg.setStyleSheet(f"color: {color};")
        self.msg.setText(text)

    def _dirty(self):
        self.msg.setText("")
        self._paint_fields()
        self.autosave.touch()

    def _paint_fields(self):
        """Статус и подсказка у каждого поля."""
        busy = self.state == "busy"
        for key, e in self.edits.items():
            v = e.text().strip()
            f = next(x for x in self.s.fields if x.key == key)
            wrong = K.wrong_key_hint(key, v)
            if not v:
                text, fg, bg = ("Не нужно" if f.optional else "Нет ключа"), C.TEXT_DIM, C.DARK
            elif wrong:
                text, fg, bg = "Не тот ключ", C.ACC2, "#15160a"
            elif busy:
                text, fg, bg = "Проверяю…", C.TEXT_MED, C.DARK
            elif self.state == "ok":
                text, fg, bg = "✓ Работает", C.PRI, C.PRI_GHO
            elif self.state in ("bad", "warn"):
                text, fg, bg = "✕ Ошибка", C.RED, "#1a0a0e"
            else:
                text, fg, bg = "Сохранено", C.TEXT_MED, C.DARK
            chip = self.chips[key]
            chip.setText(text)
            chip.setStyleSheet(f"color: {fg}; background: {bg}; border: 1px solid {C.BORDER_B};"
                               " border-radius: 10px; padding: 3px 10px; font-size: 11px; font-weight: 600;")
            note = self.notes[key]
            if wrong:
                note.setStyleSheet(f"color: {C.ACC2};")
                note.setText(wrong)
            elif v and f.secret and e.echoMode() == QLineEdit.EchoMode.Password:
                note.setStyleSheet(f"color: {C.TEXT_DIM};")
                note.setText("Сохранён: " + K.preview(v))
            else:
                note.setText("")
            note.setVisible(bool(note.text()))

    def _autosave(self):
        """Вставили ключ — он уже сохранён; все поля сервиса на месте — сразу проверяем.
        Ключ не того сервиса не сохраняем — под полем подсказка, чей он."""
        vals = {k: v for k, v in self.values().items() if not K.wrong_key_hint(k, v)}
        if not vals or vals == {k: v for k, v in K.load_values().items() if k in vals}:
            return
        K.save_values(vals)
        self.saved.emit()
        self.changed.emit()
        if K.status(self.s, K.load_values()) == "set":
            self.run_check()

    def values(self) -> dict:
        return {k: e.text().strip() for k, e in self.edits.items() if e.isEnabled()}

    # ── действия ─────────────────────────────────────────────────────────────
    def save_and_check(self):
        """«Проверить»: несохранённое — на диск, затем проверка."""
        self.autosave.cancel()
        vals = self.values()
        if vals:
            K.save_values(vals)
            self.saved.emit()
        self.changed.emit()
        self.run_check()

    def run_check(self):
        vals = K.load_values()
        self.state = "busy"
        self._paint()
        self.save_btn.setEnabled(False)
        self._say("Проверяю…", "busy")

        def work():
            st, text = K.check(self.s.id, vals)
            self.checked.emit(self.s.id, st, text)
        threading.Thread(target=work, daemon=True, name=f"key-{self.s.id}").start()

    def _on_checked(self, _sid: str, st: str, text: str):
        self.state = st
        self.save_btn.setEnabled(True)
        self._paint()
        self._say(text, st)
        if st in ("bad", "warn") and not self.body.isVisible():
            self.toggle(True)
        self.changed.emit()

    def run_action(self):
        if self.s.action == "spotify":
            self._say("Открыл браузер — войдите в Spotify и нажмите «Принять». Жду до 2,5 минут…", "busy")
            self.action_btn.setEnabled(False)

            def work():
                try:
                    from actions import spotify_premium
                    spotify_premium._auth = None
                    text = spotify_premium.login()
                except Exception as exc:
                    text = f"Вход не удался: {exc}"
                self.checked.emit(self.s.id, "ok" if text.startswith("Spotify подключён") else "bad", text)
            threading.Thread(target=work, daemon=True, name="spotify-login").start()
            self.action_btn.setEnabled(True)
        elif self.s.action == "caller":
            from core import tg_call

            def ask(text: str, secret: bool) -> str:
                val, ok = QInputDialog.getText(self, "ДЖАРВИС — звонки", text,
                                               QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
                return val.strip() if ok else ""
            self._say("Откроется QR-код — отсканируйте его телефоном с аккаунтом Джарвиса.", "busy")
            text = tg_call.login(ask, tg_call._QrDialog())
            self._say(text, "ok" if text.startswith("Готово") else "bad")
            self.run_check()


class KeysDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ДЖАРВИС — ключи и подключения")
        self.setStyleSheet(STYLE + EXTRA_STYLE)
        self.resize(900, 760)
        self.setMinimumSize(760, 560)
        values = K.load_values()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(_line())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("canvas")
        outer = QHBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        col = QVBoxLayout()
        col.setContentsMargins(28, 22, 28, 28)
        col.setSpacing(12)
        col.addWidget(self._summary())
        col.addSpacing(4)
        self.cards: dict[str, ServiceCard] = {}
        for s in K.SERVICES:
            card = ServiceCard(s, values)
            card.changed.connect(self._update_summary)
            card.saved.connect(lambda: self.saved.show_note())
            self.cards[s.id] = card
            col.addWidget(card)
        col.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(col)
        wrap.setMaximumWidth(860)
        outer.addStretch(1)
        outer.addWidget(wrap, 100)
        outer.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self._update_summary()

    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("key", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label("Джарвис", "brand"))
        col.addWidget(_label("Ключи и подключения", "h1", wrap=False))
        col.addWidget(_label("Всё сохраняется сразу", "hint", wrap=False))
        lay.addLayout(col)
        lay.addStretch(1)
        self.saved = SavedNote()
        lay.addWidget(self.saved)
        self.check_all_btn = QPushButton("  Проверить все")
        self.check_all_btn.setIcon(qicon("reload", 14, C.TEXT_MED))
        self.check_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_all_btn.clicked.connect(self.check_all)
        lay.addWidget(self.check_all_btn)
        return w

    def _summary(self) -> QWidget:
        card = QFrame()
        card.setObjectName("ai")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(IconBadge("lock", 32))
        t = QVBoxLayout()
        t.setSpacing(1)
        self.summary = _label("", "h2")
        t.addWidget(self.summary)
        t.addWidget(_label("Ключи хранятся только на этом компьютере и никуда не отправляются. "
                           "Проверка — один короткий запрос к самому сервису.", "hint"))
        top.addLayout(t, 1)
        lay.addLayout(top)
        self.progress = Progress()
        lay.addWidget(self.progress)
        return card

    def _update_summary(self):
        vals = K.load_values()
        total = len(K.SERVICES)
        done = sum(1 for s in K.SERVICES
                   if self.cards[s.id].state == "ok"
                   or (self.cards[s.id].state in ("set", "warn") and K.status(s, vals) == "set"))
        self.summary.setText(f"Подключено {done} из {total}")
        self.progress.value = done / total
        self.progress.update()

    def flush(self):
        """Ушли с экрана — сохранить то, что ещё не успело."""
        for card in getattr(self, "cards", {}).values():
            card.autosave.flush()

    def hideEvent(self, ev):
        self.flush()
        super().hideEvent(ev)

    def check_all(self):
        vals = K.load_values()
        for s in K.SERVICES:
            if K.status(s, vals) != "missing":
                self.cards[s.id].run_check()


def open_dialog(parent=None) -> KeysDialog:
    dlg = KeysDialog(parent)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
