"""Окно «Резервная копия».

Две карточки — два действия:
  1. «Сохранить копию» — что войдёт и что нет, пароль дважды, кнопка;
  2. «Восстановить» — выбрать файл, пароль, «Открыть» показывает дату и
     состав, и только потом — «Восстановить» с подтверждением.
Работа — в фоне (scrypt нарочно медленный), итог — строкой внизу карточки.
Логика — core/backup.py; вид — общий с остальными окнами (ui_kit, палитра ui.C).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
                             QVBoxLayout, QWidget)

from core import backup as B
from ui import C
from ui_icons import qicon
from ui_kit import STYLE, IconBadge, _cap, _label, _line, _small_icon

logger = logging.getLogger(__name__)

INCLUDED = "Ключи, свои команды, контакты, «Обо мне», учёба, будильники, голос, память Джарвиса"
EXCLUDED = "Не входит: вход в Telegram (после восстановления — по QR заново), заметки Obsidian, модели"


def _documents() -> Path:
    d = Path.home() / "Documents"
    return d if d.is_dir() else Path.home()


class BackupDialog(QDialog):
    _done = pyqtSignal(str, bool, object)          # какая карточка, успех, данные / текст ошибки

    def __init__(self, parent=None, root: Path | None = None, keys: dict | None = None, save_keys=None):
        super().__init__(parent)
        self.root, self.keys, self.save_keys = root, keys, save_keys      # подмена — для тестов
        self.src: Path | None = None
        self.setWindowTitle("ДЖАРВИС — резервная копия")
        self.setStyleSheet(STYLE)
        self.resize(640, 700)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._header())
        lay.addWidget(_line())
        body = QWidget()
        body.setObjectName("canvas")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(26, 22, 26, 22)
        bl.setSpacing(16)
        bl.addWidget(self._save_card())
        bl.addWidget(self._restore_card())
        bl.addStretch(1)
        lay.addWidget(body, 1)
        self._done.connect(self._on_done)

    # ── вид ──────────────────────────────────────────────────────────────────
    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("copy", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label("Джарвис", "brand"))
        col.addWidget(_label("Резервная копия", "h1"))
        lay.addLayout(col, 1)
        return w

    @staticmethod
    def _card(icon: str, title: str, hint: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(IconBadge(icon, 36))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_label(title, "h2"))
        col.addWidget(_label(hint, "hint"))
        head.addLayout(col, 1)
        lay.addLayout(head)
        return card, lay

    @staticmethod
    def _password(placeholder: str) -> QLineEdit:
        e = QLineEdit(placeholderText=placeholder)
        e.setEchoMode(QLineEdit.EchoMode.Password)
        return e

    @staticmethod
    def _button(text: str, icon: str, primary: bool = False) -> QPushButton:
        b = QPushButton("  " + text)
        if primary:
            b.setObjectName("primary")
        b.setIcon(qicon(icon, 14, C.BG if primary else C.TEXT_MED))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _save_card(self) -> QWidget:
        card, lay = self._card("down", "Сохранить копию", "Один файл — на флешку, в облако, куда удобно")
        what = QFrame()
        what.setObjectName("step")
        wl = QVBoxLayout(what)
        wl.setContentsMargins(14, 10, 14, 10)
        wl.setSpacing(6)
        for icon, text, color in (("check", INCLUDED, C.TEXT), ("close", EXCLUDED, C.TEXT_DIM)):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(_small_icon(icon, 12), 0, Qt.AlignmentFlag.AlignTop)
            lbl = _label(text, "hint")
            lbl.setStyleSheet(f"color: {color};")
            row.addWidget(lbl, 1)
            wl.addLayout(row)
        lay.addWidget(what)
        lay.addWidget(_cap("Пароль копии"))
        self.pw1 = self._password(f"Не короче {B.MIN_PASSWORD} символов")
        self.pw2 = self._password("Ещё раз")
        pw = QHBoxLayout()
        pw.setSpacing(8)
        pw.addWidget(self.pw1)
        pw.addWidget(self.pw2)
        lay.addLayout(pw)
        lay.addWidget(_label("Без пароля копию не открыть — ни вам, ни чужим. Запишите его.", "hint"))
        row = QHBoxLayout()
        self.save_status = _label("", "status")
        row.addWidget(self.save_status, 1)
        self.save_btn = self._button("Сохранить…", "down", primary=True)
        self.save_btn.clicked.connect(self.ask_save)
        row.addWidget(self.save_btn)
        lay.addLayout(row)
        return card

    def _restore_card(self) -> QWidget:
        card, lay = self._card("up", "Восстановить", "Новый ПК или переустановка — всё вернётся как было")
        pick = QHBoxLayout()
        pick.setSpacing(8)
        self.file_lbl = _label("Файл не выбран", "hint")
        pick.addWidget(self.file_lbl, 1)
        choose = self._button("Выбрать файл…", "grid")
        choose.clicked.connect(self.ask_file)
        pick.addWidget(choose)
        lay.addLayout(pick)
        self.pw_in = self._password("Пароль копии")
        self.pw_in.returnPressed.connect(self.open_backup)
        lay.addWidget(self.pw_in)
        self.info = _label("", "hint")
        self.info.setVisible(False)
        lay.addWidget(self.info)
        row = QHBoxLayout()
        self.restore_status = _label("", "status")
        row.addWidget(self.restore_status, 1)
        self.open_btn = self._button("Открыть", "eye")
        self.open_btn.clicked.connect(self.open_backup)
        # Заменяет все данные — красная, как «Удалить» в настройках iPhone (подтверждение — в ask_restore).
        self.restore_btn = self._button("Восстановить", "up")
        self.restore_btn.setObjectName("danger")
        self.restore_btn.setIcon(qicon("up", 14, C.RED))
        self.restore_btn.clicked.connect(self.ask_restore)
        self.restore_btn.setEnabled(False)
        row.addWidget(self.open_btn)
        row.addWidget(self.restore_btn)
        lay.addLayout(row)
        return card

    def _say(self, which: str, text: str, ok: bool = True):
        lbl = self.save_status if which == "save" else self.restore_status
        lbl.setStyleSheet(f"color: {C.PRI if ok else C.ACC};")
        lbl.setText(text)

    # ── действия ─────────────────────────────────────────────────────────────
    def check_passwords(self) -> str | None:
        a, b = self.pw1.text(), self.pw2.text()
        if len(a) < B.MIN_PASSWORD:
            return f"Пароль — хотя бы {B.MIN_PASSWORD} символов."
        if a != b:
            return "Пароли не совпадают."
        return None

    def ask_save(self):
        err = self.check_passwords()
        if err:
            self._say("save", err, False)
            return
        path, _f = QFileDialog.getSaveFileName(self, "Сохранить копию", str(_documents() / B.default_name()),
                                               f"Копия Джарвиса (*{B.SUFFIX})")
        if path:
            self.save_to(Path(path))

    def save_to(self, path: Path):
        pw = self.pw1.text()
        self.save_btn.setEnabled(False)
        self._say("save", "Шифрую…")

        def work():
            try:
                self._done.emit("save", True, B.create(path, pw, root=self.root, keys=self.keys))
            except Exception as exc:
                logger.warning("Копия не сохранилась: %s", exc)
                self._done.emit("save", False, str(exc))
        threading.Thread(target=work, daemon=True, name="backup").start()

    def ask_file(self):
        path, _f = QFileDialog.getOpenFileName(self, "Копия Джарвиса", str(_documents()),
                                               f"Копия Джарвиса (*{B.SUFFIX});;Все файлы (*)")
        if path:
            self.set_file(Path(path))

    def set_file(self, path: Path):
        self.src = path
        self.file_lbl.setText(path.name)
        self.file_lbl.setStyleSheet(f"color: {C.TEXT};")
        self.info.setVisible(False)
        self.restore_btn.setEnabled(False)
        self._say("restore", "")
        self.pw_in.setFocus()

    def _run(self, which: str, fn):
        self.open_btn.setEnabled(False)
        self.restore_btn.setEnabled(False)

        def work():
            try:
                self._done.emit(which, True, fn())
            except Exception as exc:
                self._done.emit(which, False, str(exc))
        threading.Thread(target=work, daemon=True, name="backup").start()

    def open_backup(self):
        if not self.src:
            self._say("restore", "Сначала выберите файл копии.", False)
            return
        src, pw = self.src, self.pw_in.text()
        self._say("restore", "Открываю…")
        self._run("inspect", lambda: B.inspect(src, pw))

    def ask_restore(self):
        q = QMessageBox.question(self, "Восстановить?",
                                 "Текущие команды, контакты, память и ключи заменятся данными из копии.\n"
                                 "Текущее состояние сначала сохранится рядом с копией — можно будет вернуть.")
        if q == QMessageBox.StandardButton.Yes:
            self.restore_now()

    def restore_now(self):
        src, pw = self.src, self.pw_in.text()
        self._say("restore", "Восстанавливаю…")
        self._run("restore", lambda: B.restore(src, pw, root=self.root, save_keys=self.save_keys,
                                                            keys_now=self.keys))

    def _on_done(self, which: str, ok: bool, data):
        if which == "save":
            self.save_btn.setEnabled(True)
            if ok:
                self._say("save", f"✓  Сохранено: {Path(data['path']).name} — файлов {data['files']}, "
                                  f"ключей {data['keys']}.")
                self.pw1.clear()
                self.pw2.clear()
            else:
                self._say("save", data, False)
            return
        self.open_btn.setEnabled(True)
        if not ok:
            self._say("restore", data, False)
            return
        if which == "inspect":
            when = data["created"].replace("T", " ")[:16]
            self.info.setText(f"Копия от {when}: файлов {len(data['files'])}, ключей {data['keys']}.")
            self.info.setStyleSheet(f"color: {C.TEXT};")
            self.info.setVisible(True)
            self.restore_btn.setEnabled(True)
            self._say("restore", "Пароль верный — можно восстанавливать.")
        else:
            self._say("restore", f"✓  Восстановлено: файлов {data['files']}, ключей {data['keys']}. "
                                 "Перезапустите Джарвиса, чтобы всё подхватилось.")


def open_dialog(parent=None) -> BackupDialog:
    dlg = BackupDialog(parent)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
