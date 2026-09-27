"""Окно «Контакты».

Слева — люди (с аватаром-инициалами), сверху справа — два подключения:
ВАШ Telegram (сообщения и звонки от вашего имени, входящие) и аккаунт
Джарвиса (звонки вам самому; людям — если ваш Telegram не подключён). Ниже — карточка человека: имя, как вы его называете (чипсы
«мама», «мамочка»), Telegram и три переключателя — что Джарвису можно:
писать, звонить, читать вам его сообщения.

Логика — core/contacts.py; вид — общий (ui_kit).
"""
from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QInputDialog, QLineEdit, QListWidget, QListWidgetItem,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from core import contacts as CT
from ui import C
from ui_icons import qicon
from ui_kit import STYLE, EmptyArt, FlowLayout, IconBadge, Toggle, _cap, _icon_btn, _label, _line, _small_icon

logger = logging.getLogger(__name__)

EXTRA = f"""
QFrame#tile {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 12px; }}
QLabel#ok {{ color: {C.PRI}; font-size: 12px; }}
QLabel#no {{ color: {C.ACC2}; font-size: 12px; }}
"""


def initials(name: str) -> str:
    parts = [p for p in (name or "?").replace("@", "").split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def avatar_pixmap(name: str, size: int = 40) -> QPixmap:
    """Кружок с инициалами — оттенок от имени, чтобы люди различались."""
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    hue = (sum(map(ord, name or "?")) * 37) % 360
    base = QColor.fromHsl(hue, 110, 46)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(base.red(), base.green(), base.blue(), 60))
    p.drawEllipse(QRectF(0, 0, size * scale, size * scale))
    p.setPen(QColor.fromHsl(hue, 160, 178))
    f = QFont("Segoe UI", int(size * scale * 0.34))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, size * scale, size * scale), int(Qt.AlignmentFlag.AlignCenter), initials(name))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


class Avatar(QWidget):
    def __init__(self, size: int = 56):
        super().__init__()
        self.name = ""
        self.setFixedSize(size, size)

    def set_name(self, name: str):
        self.name = name
        self.update()

    def paintEvent(self, _):
        QPainter(self).drawPixmap(0, 0, avatar_pixmap(self.name, self.width()))


class AliasChip(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, text: str):
        super().__init__()
        self.text = text
        self.setObjectName("chip")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 3, 3, 3)
        lay.setSpacing(2)
        lbl = _label(text, wrap=False)
        lbl.setStyleSheet(f"color: {C.WHITE};")
        x = _icon_btn("close", "Убрать", 10)
        x.setFixedSize(24, 24)
        x.clicked.connect(lambda: self.removed.emit(self.text))
        lay.addWidget(lbl)
        lay.addWidget(x)


class ContactsDialog(QDialog):
    _done = pyqtSignal(str, str)                    # что сделано, текст

    def __init__(self, parent=None, api: CT.Contacts | None = None, caller_ready=None, open_keys=None):
        super().__init__(parent)
        self.api = api or CT.contacts()
        self.book = self.api.book
        self.caller_ready = caller_ready or _caller_ready
        self.open_keys = open_keys or _open_keys
        self.current: CT.Contact | None = None
        self._aliases: list[str] = []
        self.setWindowTitle("ДЖАРВИС — контакты")
        self.setStyleSheet(STYLE + EXTRA)
        self.resize(1060, 720)
        self.setMinimumSize(900, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(_line())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._sidebar())
        right = QVBoxLayout()
        right.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName("canvas")
        self.form = QVBoxLayout(canvas)
        self.form.setContentsMargins(30, 22, 30, 24)
        self.form.setSpacing(10)
        self._build_accounts()
        self._build_editor()
        self.form.addStretch(1)
        scroll.setWidget(canvas)
        right.addWidget(scroll, 1)
        right.addWidget(_line())
        right.addWidget(self._footer())
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        self._done.connect(self._on_done)
        self.reload()
        self.refresh_accounts()
        if self.book.contacts:
            self.people.setCurrentRow(0)
        else:
            self.new_contact()

    # ── шапка ────────────────────────────────────────────────────────────────
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
        col.addWidget(_label("Контакты", "h1", wrap=False))
        lay.addLayout(col)
        lay.addStretch(1)
        self.import_btn = QPushButton("  Подтянуть из Telegram")
        self.import_btn.setIcon(qicon("plane", 14, C.TEXT_MED))
        self.import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_btn.clicked.connect(self.import_contacts)
        lay.addWidget(self.import_btn)
        return w

    def _sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(290)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 18, 16, 16)
        sl.setSpacing(10)
        new = QPushButton("  Новый контакт")
        new.setObjectName("primary")
        new.setIcon(qicon("plus", 14, C.BG))
        new.setCursor(Qt.CursorShape.PointingHandCursor)
        new.clicked.connect(self.new_contact)
        sl.addWidget(new)
        self.search = QLineEdit(placeholderText="Найти человека")
        self.search.textChanged.connect(self.reload)
        sl.addWidget(self.search)
        sl.addSpacing(4)
        self.count_cap = _cap("Люди")
        sl.addWidget(self.count_cap)
        self.people = QListWidget()
        self.people.setIconSize(QSize(34, 34))
        self.people.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.people.currentItemChanged.connect(
            lambda cur, _p: cur and self.show_contact(cur.data(Qt.ItemDataRole.UserRole)))
        sl.addWidget(self.people, 1)
        self.empty = EmptyArt("contacts", "Пока пусто.\n\nПодключите свой Telegram и нажмите\n"
                                          "«Подтянуть из Telegram» — или добавьте человека вручную.")
        sl.addWidget(self.empty, 1)
        tip = QFrame()
        tip.setObjectName("card")
        tl = QHBoxLayout(tip)
        tl.setContentsMargins(12, 10, 12, 10)
        tl.addWidget(_small_icon("mic", 14), 0, Qt.AlignmentFlag.AlignTop)
        tl.addWidget(_label("«Джарвис, напиши маме, что задержусь» — он переспросит, кому и что, "
                            "и только после «да» отправит.", "hint"), 1)
        sl.addWidget(tip)
        return side

    # ── подключения ──────────────────────────────────────────────────────────
    def _tile(self, icon: str, title: str, what: str) -> tuple[QFrame, object, QPushButton]:
        tile = QFrame()
        tile.setObjectName("tile")
        lay = QHBoxLayout(tile)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        lay.addWidget(IconBadge(icon, 34))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label(title, "stepTitle"))
        col.addWidget(_label(what, "hint"))
        state = _label("", "ok")
        col.addWidget(state)
        lay.addLayout(col, 1)
        btn = QPushButton()
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
        return tile, state, btn

    def _build_accounts(self):
        self.form.addWidget(self._section("plane", "Подключения"))
        row = QHBoxLayout()
        row.setSpacing(12)
        t1, self.me_state, self.me_btn = self._tile("plane", "Ваш Telegram", "Пишет и звонит от вас, читает входящие")
        self.me_btn.clicked.connect(self.link_me)
        t2, self.caller_state, self.caller_btn = self._tile("phone", "Аккаунт Джарвиса", "Звонит вам; людям — если ваш не подключён")
        self.caller_btn.setText("Настроить")
        self.caller_btn.clicked.connect(self.open_keys)
        row.addWidget(t1, 1)
        row.addWidget(t2, 1)
        self.form.addLayout(row)
        quiet = QFrame()
        quiet.setObjectName("tile")
        ql = QHBoxLayout(quiet)
        ql.setContentsMargins(14, 10, 14, 10)
        ql.setSpacing(12)
        ql.addWidget(_small_icon("moon", 16))
        a, b = self.book.quiet
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(_label(f"Не звонить ночью · {a}:00–{b:02d}:00", "stepTitle"))
        col.addWidget(_label("Кроме случаев, когда вы сказали «срочно»", "hint"))
        ql.addLayout(col, 1)
        self.quiet = Toggle()
        self.quiet.setChecked(self.book.quiet_on)
        self.quiet.toggled.connect(self._set_quiet)
        ql.addWidget(self.quiet)
        self.form.addWidget(quiet)
        self.form.addSpacing(10)

    def refresh_accounts(self):
        linked = self.api.me.linked()
        self.me_state.setObjectName("ok" if linked else "no")
        self.me_state.setText("●  Подключён" if linked else "●  Не подключён")
        self.me_btn.setText("Переподключить" if linked else "Подключить по QR")
        self.me_btn.setObjectName("" if linked else "primary")
        problem = self.caller_ready()
        self.caller_state.setObjectName("no" if problem else "ok")
        self.caller_state.setText("●  Готов звонить" if not problem else "●  Не настроен")
        self.caller_state.setToolTip(problem)
        self.import_btn.setEnabled(linked)
        for w in (self.me_state, self.me_btn, self.caller_state):
            w.style().unpolish(w)
            w.style().polish(w)

    def _set_quiet(self, on: bool):
        self.book.quiet_on = on
        self.book.save()

    # ── карточка человека ────────────────────────────────────────────────────
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

    def _build_editor(self):
        top = QHBoxLayout()
        top.setSpacing(14)
        self.avatar = Avatar(56)
        top.addWidget(self.avatar)
        self.name = QLineEdit(placeholderText="Имя — «Мама», «Азиз Каримов»")
        self.name.setObjectName("title")
        self.name.textChanged.connect(self.avatar.set_name)
        top.addWidget(self.name, 1)
        self.form.addLayout(top)

        self.form.addSpacing(4)
        self.form.addWidget(self._section("mic", "Как вы его называете", "Джарвис поймёт любое из этих слов"))
        chips = QWidget()
        self.chips = FlowLayout(chips)
        self.form.addWidget(chips)
        self.alias_in = QLineEdit(placeholderText="Добавить — «мама», «мамочка», «брат» и Enter")
        self.alias_in.returnPressed.connect(lambda: self.add_alias(self.alias_in.text()))
        self.form.addWidget(self.alias_in)

        self.form.addSpacing(8)
        self.form.addWidget(self._section("plane", "Telegram", "@username, ссылка t.me или номер телефона"))
        self.telegram = QLineEdit(placeholderText="@username или +7 999 123-45-67")
        self.form.addWidget(self.telegram)

        self.form.addSpacing(8)
        self.form.addWidget(self._section("lock", "Что Джарвису можно"))
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 4, 16, 4)
        cl.setSpacing(0)

        def row(title, hint, sep=True):
            w = QWidget()
            r = QHBoxLayout(w)
            r.setContentsMargins(0, 10, 0, 10)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(_label(title, "stepTitle"))
            col.addWidget(_label(hint, "hint"))
            r.addLayout(col, 1)
            t = Toggle()
            r.addWidget(t)
            cl.addWidget(w)
            if sep:
                cl.addWidget(_line())
            return t
        self.can_message = row("Писать сообщения", "От вашего имени, всегда с вашим «да»")
        self.can_call = row("Звонить", "Джарвис звонит с вашего Telegram и передаёт ваши слова")
        self.read_aloud = row("Читать мне его сообщения", "Новое — в капсуле, «что мне написали?» — вслух",
                              sep=False)
        self.form.addWidget(card)

        self.form.addSpacing(8)
        self.form.addWidget(self._section("note", "Заметка", "Джарвис учтёт: «говорить на вы», «глуховат»"))
        self.note = QLineEdit(placeholderText="Необязательно")
        self.form.addWidget(self.note)

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
        self.del_btn.clicked.connect(self.delete_contact)
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        save.setFixedWidth(130)
        save.clicked.connect(self.save_contact)
        for b in (self.del_btn, save):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(b)
        return w

    # ── список ───────────────────────────────────────────────────────────────
    def reload(self):
        q = CT._norm(self.search.text())
        self.people.blockSignals(True)
        self.people.clear()
        people = sorted(self.book.contacts, key=lambda c: c.name.lower())
        for c in people:
            if q and q not in CT._norm(" ".join([c.name, c.telegram, *c.aliases])):
                continue
            sub = ", ".join(c.aliases) or c.telegram or "нет Telegram"
            item = QListWidgetItem(f"{c.name}\n{sub}")
            item.setIcon(QIcon(avatar_pixmap(c.name, 34)))
            item.setData(Qt.ItemDataRole.UserRole, c.id)
            self.people.addItem(item)
            if self.current and c.id == self.current.id:
                self.people.setCurrentItem(item)
        self.people.blockSignals(False)
        n = len(people)
        self.count_cap.setText(f"ЛЮДИ · {n}" if n else "ЛЮДИ")
        self.empty.setVisible(not people)
        self.people.setVisible(bool(people))

    def show_contact(self, cid):
        c = next((x for x in self.book.contacts if x.id == cid), None)
        self.current = c
        c = c or CT.Contact(name="")
        self.name.setText(c.name)
        self.avatar.set_name(c.name)
        self._aliases = list(c.aliases)
        self._render_chips()
        self.telegram.setText(c.telegram)
        self.can_message.setChecked(c.can_message)
        self.can_call.setChecked(c.can_call)
        self.read_aloud.setChecked(c.read_aloud)
        self.note.setText(c.note)
        self.del_btn.setVisible(self.current is not None)
        self.status.setText("")

    def new_contact(self):
        self.people.clearSelection()
        self.show_contact(None)
        self.name.setFocus()

    def add_alias(self, text: str):
        text = " ".join((text or "").split()).strip(" «»\"").lower()
        if text and text not in self._aliases:
            self._aliases.append(text)
            self._render_chips()
        self.alias_in.clear()

    def _remove_alias(self, text: str):
        self._aliases = [a for a in self._aliases if a != text]
        self._render_chips()

    def _render_chips(self):
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for a in self._aliases:
            chip = AliasChip(a)
            chip.removed.connect(self._remove_alias)
            self.chips.addWidget(chip)
        self.chips.parentWidget().setVisible(bool(self._aliases))

    def _say(self, text: str, ok: bool = True):
        self.status.setStyleSheet(f"color: {C.PRI if ok else C.ACC};")
        self.status.setText(text)

    def save_contact(self) -> bool:
        name = self.name.text().strip()
        if not name:
            self._say("Как зовут человека?", False)
            self.name.setFocus()
            return False
        raw = self.telegram.text().strip()
        tg = CT.parse_telegram(raw)
        if raw and not tg:
            self._say("Telegram — @username (от 5 букв) или номер телефона с кодом страны.", False)
            self.telegram.setFocus()
            return False
        if self.alias_in.text().strip():
            self.add_alias(self.alias_in.text())
        cur = self.current
        c = CT.Contact(name=name, telegram=tg, aliases=list(self._aliases), can_message=self.can_message.isChecked(),
                       can_call=self.can_call.isChecked(), read_aloud=self.read_aloud.isChecked(),
                       note=self.note.text().strip(),
                       tg_id=cur.tg_id if cur and cur.telegram == tg else 0,
                       id=cur.id if cur else CT.Contact(name="").id)
        self.book.upsert(c)
        self.current = c
        self.telegram.setText(tg)
        self.reload()
        self.del_btn.setVisible(True)
        call = self.aliases_hint(c)
        self._say(f"✓  Сохранено. Скажите: «Джарвис, напиши {call}…»" if tg
                  else "✓  Сохранено. Добавьте Telegram — без него писать и звонить некуда.", bool(tg))
        return True

    @staticmethod
    def aliases_hint(c: CT.Contact) -> str:
        return (c.aliases[0] if c.aliases else c.name.split()[0]).lower()

    def delete_contact(self):
        if self.current and self.book.delete(self.current.id):
            name = self.current.name
            self.new_contact()
            self.reload()
            self._say(f"«{name}» удалён.")

    # ── действия с Telegram ─────────────────────────────────────────────────
    def link_me(self):
        from core import tg_call

        def ask(text: str, secret: bool) -> str:
            val, ok = QInputDialog.getText(self, "ДЖАРВИС — ваш Telegram", text.replace("аккаунта Джарвиса", "вашего аккаунта"),
                                           QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
            return val.strip() if ok else ""
        self._say("Откроется QR-код: ВАШ Telegram → Настройки → Устройства → Подключить устройство.")
        text = CT.login(ask, tg_call._QrDialog())
        self._say(text, text.startswith("Готово"))
        self.refresh_accounts()

    def import_contacts(self):
        self.import_btn.setEnabled(False)
        self._say("Подтягиваю контакты из вашего Telegram…")

        def work():
            self._done.emit("import", self.api.import_from_telegram())
        threading.Thread(target=work, daemon=True, name="contacts-import").start()

    def _on_done(self, what: str, text: str):
        self.import_btn.setEnabled(self.api.me.linked())
        self._say(text, not text.startswith("Не "))
        self.reload()


def _caller_ready() -> str:
    try:
        from core import tg_call
        problem = tg_call.ready()
        return "" if "Не знаю, кому звонить" in problem else problem
    except Exception as exc:
        return str(exc)


def _open_keys():
    from ui_keys import open_dialog
    open_dialog(None)


def open_dialog(parent=None) -> ContactsDialog:
    dlg = ContactsDialog(parent)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
