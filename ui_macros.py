"""Окно «Свои команды»: список команд, фразы запуска, шаги, «Собрать с
помощью ИИ» по описанию словами и готовые паки для программ.

Сами команды живут в core/macros.py; окно только редактирует их."""
from __future__ import annotations

import json
import logging
import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QTableWidget,
                             QTabWidget, QVBoxLayout, QWidget)

from core import macros as mc
from core.macro_packs import PACKS

logger = logging.getLogger(__name__)

STYLE = """
QDialog, QWidget { background: #0d1117; color: #e6edf3; font-family: 'Segoe UI'; font-size: 13px; }
QTabWidget::pane { border: 1px solid #22303c; border-radius: 10px; }
QTabBar::tab { background: #121a24; padding: 8px 18px; border-radius: 8px; margin: 4px; }
QTabBar::tab:selected { background: #1f6feb; color: white; }
QLineEdit, QPlainTextEdit, QComboBox, QTableWidget, QListWidget {
  background: #121a24; border: 1px solid #22303c; border-radius: 8px; padding: 6px; }
QListWidget::item { padding: 8px; border-radius: 6px; }
QListWidget::item:selected { background: #1f3b61; }
QPushButton { background: #1b2530; border: 1px solid #2a3947; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #243242; }
QPushButton#primary { background: #1f6feb; border: none; color: white; font-weight: 600; }
QHeaderView::section { background: #121a24; color: #8b98a5; border: none; padding: 4px; }
QLabel#hint { color: #8b98a5; font-size: 12px; }
QLabel#title { font-size: 18px; font-weight: 700; }
"""

TYPES = list(mc.STEP_TYPES)


class MacrosDialog(QDialog):
    _ai_done = pyqtSignal(object, str)

    def __init__(self, parent=None, store: mc.Macros | None = None, build=None):
        super().__init__(parent)
        self.store = store or mc.macros()
        self.build = build or mc.build_with_ai
        self.current: mc.Command | None = None
        self.setWindowTitle("ДЖАРВИС — свои команды")
        self.setStyleSheet(STYLE)
        self.resize(980, 640)
        tabs = QTabWidget()
        tabs.addTab(self._commands_tab(), "Свои команды")
        tabs.addTab(self._packs_tab(), "Паки программ")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        self._ai_done.connect(self._on_ai)
        self.reload()

    # ── вкладка команд ───────────────────────────────────────────────────────
    def _commands_tab(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        left = QVBoxLayout()
        self.search = QLineEdit(placeholderText="Поиск команды…")
        self.search.textChanged.connect(self.reload)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda cur, _prev: self.show_command(cur.data(Qt.ItemDataRole.UserRole)
                                                                                 if cur else None))
        btns = QHBoxLayout()
        new, dele = QPushButton("＋ Новая"), QPushButton("Удалить")
        new.clicked.connect(self.new_command)
        dele.clicked.connect(self.delete_command)
        btns.addWidget(new)
        btns.addWidget(dele)
        left.addWidget(self.search)
        left.addWidget(self.list, 1)
        left.addLayout(btns)

        right = QVBoxLayout()
        title = QLabel("Команда")
        title.setObjectName("title")
        right.addWidget(title)
        # ИИ: описать словами
        ai = QHBoxLayout()
        self.ai_text = QLineEdit(placeholderText="Опишите словами: «открой OBS, подожди 2 секунды и включи музыку»")
        self.ai_btn = QPushButton("✨ Собрать с помощью ИИ")
        self.ai_btn.setObjectName("primary")
        self.ai_btn.clicked.connect(self.ask_ai)
        ai.addWidget(self.ai_text, 1)
        ai.addWidget(self.ai_btn)
        right.addLayout(ai)

        self.name = QLineEdit(placeholderText="Название — «Режим стрима»")
        self.phrases = QPlainTextEdit(placeholderText="Фразы запуска, по одной в строке:\nвключи режим стрима\n"
                                                      "найди на ютубе {запрос}")
        self.phrases.setFixedHeight(80)
        opts = QHBoxLayout()
        self.app = QLineEdit(placeholderText="Только в программе (chrome.exe) — можно пусто")
        self.confirm = QCheckBox("Переспрашивать")
        self.enabled = QCheckBox("Включена")
        opts.addWidget(self.app, 1)
        opts.addWidget(self.confirm)
        opts.addWidget(self.enabled)
        for wdg in (QLabel("Название"), self.name, QLabel("Фразы для запуска"), self.phrases):
            right.addWidget(wdg)
        right.addLayout(opts)

        right.addWidget(QLabel("Шаги по порядку"))
        self.steps = QTableWidget(0, 3)
        self.steps.setHorizontalHeaderLabels(["Действие", "Значение", "Доп. (x,y / аргументы)"])
        self.steps.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.steps.setColumnWidth(0, 190)
        self.steps.setColumnWidth(2, 230)
        self.steps.verticalHeader().setVisible(False)
        right.addWidget(self.steps, 1)
        sb = QHBoxLayout()
        for text, fn in (("＋ Шаг", self.add_step), ("↑", lambda: self.move_step(-1)),
                         ("↓", lambda: self.move_step(1)), ("✕ Шаг", self.remove_step)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            sb.addWidget(b)
        sb.addStretch(1)
        self.test_btn = QPushButton("▶ Проверить")
        self.test_btn.clicked.connect(self.test_command)
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        save.clicked.connect(self.save_command)
        sb.addWidget(self.test_btn)
        sb.addWidget(save)
        right.addLayout(sb)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        right.addWidget(self.status)

        row.addLayout(left, 2)
        row.addLayout(right, 5)
        return w

    def reload(self):
        q = mc._norm(self.search.text()) if hasattr(self, "search") else ""
        self.list.clear()
        for c in sorted(self.store.commands, key=lambda c: (bool(c.pack), c.pack, c.name.lower())):
            text = f"{c.name}\n{c.phrases[0] if c.phrases else ''}"
            if q and q not in mc._norm(text):
                continue
            item = QListWidgetItem(("📦 " if c.pack else "⚡ ") + text)
            item.setData(Qt.ItemDataRole.UserRole, c.id)
            if not c.enabled:
                item.setForeground(Qt.GlobalColor.gray)
            self.list.addItem(item)

    def show_command(self, cid):
        c = next((x for x in self.store.commands if x.id == cid), None)
        self.current = c
        c = c or mc.Command("", [], [])
        self.name.setText(c.name)
        self.phrases.setPlainText("\n".join(c.phrases))
        self.app.setText(c.app)
        self.confirm.setChecked(c.confirm)
        self.enabled.setChecked(c.enabled)
        self.steps.setRowCount(0)
        for s in c.steps:
            self.add_step(s)

    def new_command(self):
        self.list.clearSelection()
        self.show_command(None)
        self.enabled.setChecked(True)
        self.name.setFocus()

    def add_step(self, step: dict | None = None):
        step = step if isinstance(step, dict) else {"do": "keys", "value": ""}
        r = self.steps.rowCount()
        self.steps.insertRow(r)
        box = QComboBox()
        for t in TYPES:
            box.addItem(mc.STEP_TYPES[t], t)
        box.setCurrentIndex(max(0, TYPES.index(step["do"]) if step["do"] in TYPES else 0))
        self.steps.setCellWidget(r, 0, box)
        self.steps.setCellWidget(r, 1, QLineEdit(str(step.get("value", ""))))
        extra = ""
        if step["do"] == "click":
            extra = f"{step.get('x', 0)},{step.get('y', 0)}"
        elif step["do"] == "tool":
            extra = f"{step.get('tool', '')} {json.dumps(step.get('args') or {}, ensure_ascii=False)}"
        self.steps.setCellWidget(r, 2, QLineEdit(extra))

    def remove_step(self):
        r = self.steps.currentRow()
        if r >= 0:
            self.steps.removeRow(r)

    def move_step(self, d: int):
        r = self.steps.currentRow()
        rows = self.read_steps()
        if r < 0 or not (0 <= r + d < len(rows)):
            return
        rows[r], rows[r + d] = rows[r + d], rows[r]
        self.steps.setRowCount(0)
        for s in rows:
            self.add_step(s)
        self.steps.setCurrentCell(r + d, 1)

    def read_steps(self) -> list[dict]:
        out = []
        for r in range(self.steps.rowCount()):
            do = self.steps.cellWidget(r, 0).currentData()
            step = {"do": do, "value": self.steps.cellWidget(r, 1).text().strip()}
            extra = self.steps.cellWidget(r, 2).text().strip()
            if do == "click":
                xy = [p for p in extra.replace(";", ",").split(",") if p.strip()]
                step["x"], step["y"] = (int(float(xy[0])), int(float(xy[1]))) if len(xy) == 2 else (0, 0)
            if do == "tool":
                tool, _, args = extra.partition(" ")
                step["tool"], step["args"] = tool, args or "{}"
            out.append(step)
        return mc.clean_steps(out)

    def form(self) -> mc.Command:
        return mc.Command(name=self.name.text().strip() or "Без названия",
                          phrases=[p.strip() for p in self.phrases.toPlainText().splitlines() if p.strip()],
                          steps=self.read_steps(), app=self.app.text().strip(), confirm=self.confirm.isChecked(),
                          enabled=self.enabled.isChecked(), pack=self.current.pack if self.current else "",
                          id=self.current.id if self.current else mc.Command("", [], []).id)

    def save_command(self) -> bool:
        c = self.form()
        if not c.phrases or not c.steps:
            self.status.setText("Нужны хотя бы одна фраза и один шаг.")
            return False
        self.store.upsert(c)
        self.current = c
        self.reload()
        self.status.setText(f"✓ Сохранено. Скажите: «{c.phrases[0]}».")
        return True

    def delete_command(self):
        if self.current and self.store.delete(self.current.id):
            self.status.setText(f"Удалена «{self.current.name}».")
            self.current = None
            self.reload()
            self.show_command(None)

    def test_command(self):
        c = self.form()
        if not c.steps:
            return
        self.status.setText(self.store.run(c) + " Переключитесь в нужное окно — через 3 с.")

    # ── ИИ ───────────────────────────────────────────────────────────────────
    def ask_ai(self):
        text = self.ai_text.text().strip()
        if not text:
            self.status.setText("Опишите, что должна делать команда.")
            return
        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("Собираю из действий…")

        def work():
            try:
                self._ai_done.emit(self.build(text), "")
            except Exception as exc:
                logger.warning("Сборка команды ИИ: %s", exc)
                self._ai_done.emit(None, str(exc))
        threading.Thread(target=work, daemon=True, name="macro-ai").start()

    def _on_ai(self, data, err: str):
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("✨ Собрать с помощью ИИ")
        if not data:
            self.status.setText(f"Не собралось: {err}")
            return
        self.current = None
        cmd = mc.Command.from_dict(data)
        self.show_command(None)
        self.name.setText(cmd.name)
        self.phrases.setPlainText("\n".join(cmd.phrases))
        self.app.setText(cmd.app)
        self.enabled.setChecked(True)
        for s in cmd.steps:
            self.add_step(s)
        self.status.setText(f"Готово — {len(cmd.steps)} шагов. Проверьте и нажмите «Сохранить».")

    # ── паки ─────────────────────────────────────────────────────────────────
    def _packs_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel("Команды пака работают, только когда эта программа впереди. Ставятся в один клик.")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        self.pack_rows: dict[str, QPushButton] = {}
        for key, p in PACKS.items():
            row = QHBoxLayout()
            text = QLabel(f"<b>{p['title']}</b> · {len(p['commands'])} команд<br>"
                          f"<span style='color:#8b98a5'>{p['about']}</span>")
            btn = QPushButton()
            btn.setFixedWidth(150)
            btn.clicked.connect(lambda _=False, k=key: self.toggle_pack(k))
            self.pack_rows[key] = btn
            row.addWidget(text, 1)
            row.addWidget(btn)
            lay.addLayout(row)
        lay.addStretch(1)
        self._paint_packs()
        return w

    def _paint_packs(self):
        for key, btn in self.pack_rows.items():
            on = key in self.store.installed
            btn.setText("✓ Установлен" if on else "Установить")
            btn.setToolTip("Нажмите, чтобы убрать пак" if on else "Поставить команды пака")
            btn.setObjectName("" if on else "primary")
            btn.setStyleSheet("")          # применить objectName

    def toggle_pack(self, key: str):
        if key in self.store.installed:
            self.store.remove_pack(key)
        else:
            self.store.install_pack(key)
        self._paint_packs()
        self.reload()


def open_dialog(parent=None) -> MacrosDialog:
    dlg = MacrosDialog(parent)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
