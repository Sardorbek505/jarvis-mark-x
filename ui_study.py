"""Окно «Учёба».

Две страницы:
  • Расписание — неделя столбцами (Пн–Сб), сегодня подсвечено; сверху
    «Сейчас / следующая пара»; у каждого дня «+ пара»; клик по паре —
    правка. Переключатель «эта неделя / следующая» — видно чётность.
  • Задачи — строка «что сделать · предмет · срок словами» и список по
    срокам: просрочено, сегодня, на неделе, позже, без срока, сделано.
Логика — core/study.py; вид — общий (ui_kit).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QPushButton,
                             QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from core import study as S
from ui import C
from ui_icons import qicon
from ui_kit import STYLE, IconBadge, _cap, _icon_btn, _label, _line

logger = logging.getLogger(__name__)

EXTRA = f"""
QFrame#day {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 12px; }}
QFrame#today {{ background: {C.PANEL}; border: 1px solid {C.PRI_DIM}; border-radius: 12px; }}
QFrame#lesson {{ background: {C.DARK}; border: 1px solid {C.BORDER}; border-radius: 9px; }}
QFrame#lesson:hover {{ border-color: {C.PRI_DIM}; }}
QFrame#task {{ background: transparent; border: none; }}
QLabel#time {{ color: {C.PRI}; font-family: Consolas; font-size: 11px; font-weight: bold; }}
QLabel#subj {{ color: {C.WHITE}; font-weight: 600; }}
QLabel#late {{ color: {C.RED}; font-size: 12px; }}
QLabel#soon {{ color: {C.ACC2}; font-size: 12px; }}
QPushButton#check {{ border: 1.5px solid {C.BORDER_B}; border-radius: 11px; padding: 0; }}
QPushButton#check:hover {{ border-color: {C.PRI}; }}
QPushButton#checked {{ border: none; border-radius: 11px; padding: 0; background: {C.PRI_DIM}; }}
"""

KIND_SHORT = {"лекция": "лек", "практика": "прак", "семинар": "сем", "лабораторная": "лаб"}


class LessonDialog(QDialog):
    """Пара: предмет, день, время, аудитория, преподаватель, тип, недели."""

    def __init__(self, parent, st: S.Study, lesson: S.Lesson | None = None, weekday: int = 0):
        super().__init__(parent)
        self.st, self.lesson = st, lesson
        self.deleted = False
        self.setWindowTitle("ДЖАРВИС — пара")
        self.setStyleSheet(STYLE + EXTRA)
        self.resize(460, 420)
        x = lesson or S.Lesson("", weekday, "08:30", "09:50")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(8)
        lay.addWidget(_label("Пара" if lesson else "Новая пара", "h1", wrap=False))
        self.subject = QComboBox()
        self.subject.setEditable(True)
        self.subject.addItems(st.subjects())
        self.subject.setCurrentText(x.subject)
        self.subject.lineEdit().setPlaceholderText("Предмет — «Математический анализ»")
        self.day = QComboBox()
        self.day.addItems([w.capitalize() for w in S.WEEKDAYS[:6]])
        self.day.setCurrentIndex(min(x.weekday, 5))
        self.start, self.end = QLineEdit(x.start), QLineEdit(x.end)
        self.start.setPlaceholderText("08:30")
        self.end.setPlaceholderText("09:50")
        self.room, self.teacher = QLineEdit(x.room), QLineEdit(x.teacher)
        self.room.setPlaceholderText("Аудитория")
        self.teacher.setPlaceholderText("Преподаватель")
        self.kind = QComboBox()
        self.kind.addItems([k.capitalize() for k in S.KINDS])
        self.kind.setCurrentIndex(S.KINDS.index(x.kind) if x.kind in S.KINDS else 0)
        self.weeks = QComboBox()
        for key, text in (("all", "Каждую неделю"), ("odd", "По нечётным"), ("even", "По чётным")):
            self.weeks.addItem(text, key)
        self.weeks.setCurrentIndex(max(0, self.weeks.findData(x.weeks)))
        for cap, w in (("Предмет", self.subject), ("День", self.day)):
            lay.addWidget(_cap(cap))
            lay.addWidget(w)
        lay.addWidget(_cap("Время"))
        t = QHBoxLayout()
        t.addWidget(self.start)
        t.addWidget(_label("—", "hint", wrap=False))
        t.addWidget(self.end)
        lay.addLayout(t)
        r = QHBoxLayout()
        r.addWidget(self.room)
        r.addWidget(self.teacher, 1)
        lay.addWidget(_cap("Где и кто"))
        lay.addLayout(r)
        r2 = QHBoxLayout()
        r2.addWidget(self.kind)
        r2.addWidget(self.weeks, 1)
        lay.addLayout(r2)
        self.msg = _label("", "hint")
        lay.addWidget(self.msg)
        foot = QHBoxLayout()
        if lesson:
            rm = QPushButton("  Удалить")
            rm.setObjectName("danger")
            rm.setIcon(qicon("trash", 13, C.TEXT_DIM))
            rm.clicked.connect(self._delete)
            foot.addWidget(rm)
        foot.addStretch(1)
        ok = QPushButton("Сохранить")
        ok.setObjectName("primary")
        ok.setFixedWidth(130)
        ok.clicked.connect(self._save)
        foot.addWidget(ok)
        lay.addLayout(foot)

    def _save(self):
        subject = self.subject.currentText().strip()
        start, end = S.hhmm(self.start.text()), S.hhmm(self.end.text())
        if not subject or not start:
            self.msg.setText("Нужны предмет и время начала.")
            return
        data = dict(subject=subject, weekday=self.day.currentIndex(), start=start, end=end,
                    room=self.room.text().strip(), teacher=self.teacher.text().strip(),
                    kind=S.KINDS[self.kind.currentIndex()], weeks=self.weeks.currentData())
        if self.lesson:
            for k, v in data.items():
                setattr(self.lesson, k, v)
        else:
            self.st.lessons.append(S.Lesson(**data))
        self.st.save()
        self.accept()

    def _delete(self):
        self.st.lessons = [x for x in self.st.lessons if x.id != self.lesson.id]
        self.st.save()
        self.deleted = True
        self.accept()


class StudyDialog(QDialog):
    def __init__(self, parent=None, st: S.Study | None = None):
        super().__init__(parent)
        self.st = st or S.study()
        self.week_offset = 0
        self.show_done = False
        self.setWindowTitle("ДЖАРВИС — учёба")
        self.setStyleSheet(STYLE + EXTRA)
        self.resize(1140, 760)
        self.setMinimumSize(960, 600)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._header())
        root.addWidget(_line())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._schedule_page())
        self.pages.addWidget(self._tasks_page())
        root.addWidget(self.pages, 1)
        self.render_week()
        self.render_tasks()

    # ── шапка ────────────────────────────────────────────────────────────────
    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("bar")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(14)
        lay.addWidget(IconBadge("book", 38))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label("ДЖАРВИС", "brand"))
        col.addWidget(_label("Учёба", "h1", wrap=False))
        lay.addLayout(col)
        lay.addStretch(1)
        seg = QFrame()
        seg.setObjectName("seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.seg_week = QPushButton("  Расписание")
        self.seg_tasks = QPushButton("  Задачи")
        for b, icon, page in ((self.seg_week, "grid", 0), (self.seg_tasks, "checks", 1)):
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setIcon(qicon(icon, 14, C.PRI))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, i=page: self.show_page(i))
            sl.addWidget(b)
        self.seg_week.setChecked(True)
        lay.addWidget(seg)
        return w

    def show_page(self, i: int):
        self.pages.setCurrentIndex(i)
        self.seg_week.setChecked(i == 0)
        self.seg_tasks.setChecked(i == 1)

    # ── расписание ───────────────────────────────────────────────────────────
    def _schedule_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("canvas")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(12)
        now = QFrame()
        now.setObjectName("ai")
        nl = QHBoxLayout(now)
        nl.setContentsMargins(16, 12, 16, 12)
        nl.setSpacing(12)
        nl.addWidget(IconBadge("timer", 32))
        col = QVBoxLayout()
        col.setSpacing(1)
        self.now_label = _label("", "h2")
        col.addWidget(self.now_label)
        col.addWidget(_label("Спросите: «Джарвис, что у меня завтра?», «какая следующая пара?» — "
                             "и он напомнит за 10 минут до пары.", "hint"))
        nl.addLayout(col, 1)
        lay.addWidget(now)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        prev, nxt = _icon_btn("back", "Прошлая неделя"), _icon_btn("next", "Следующая неделя")
        prev.clicked.connect(lambda: self.shift_week(-1))
        nxt.clicked.connect(lambda: self.shift_week(1))
        self.week_label = _label("", "stepTitle", wrap=False)
        nav.addWidget(prev)
        nav.addWidget(self.week_label)
        nav.addWidget(nxt)
        nav.addStretch(1)
        nav.addWidget(_label("Начало семестра", "hint", wrap=False))
        self.sem = QLineEdit(self.st.semester_start)
        self.sem.setPlaceholderText("2026-09-01")
        self.sem.setFixedWidth(120)
        self.sem.editingFinished.connect(self._set_semester)
        nav.addWidget(self.sem)
        lay.addLayout(nav)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("canvas")
        self.grid = QGridLayout(body)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        return page

    def shift_week(self, d: int):
        self.week_offset += d
        self.render_week()

    def _set_semester(self):
        text = self.sem.text().strip()
        try:
            date.fromisoformat(text)
        except ValueError:
            d = S.parse_due(text, self.st.now().date())
            text = d.isoformat() if d else ""
            self.sem.setText(text)
        self.st.semester_start = text
        self.st.save()
        self.render_week()

    def render_week(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        today = self.st.now().date()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=self.week_offset)
        parity = "нечётная" if self.st.week_parity(monday) == "odd" else "чётная"
        when = {0: "Эта неделя", 1: "Следующая неделя", -1: "Прошлая неделя"}.get(self.week_offset, "Неделя")
        self.week_label.setText(f"{when} · {monday.day}.{monday.month:02d}–{(monday + timedelta(days=5)).day}."
                                f"{(monday + timedelta(days=5)).month:02d} · {parity}")
        self.now_label.setText(self.st.now_next() if self.st.lessons else
                               "Расписания пока нет — добавьте пары: «+ пара» у нужного дня.")
        for i in range(6):
            d = monday + timedelta(days=i)
            self.grid.addWidget(self._day(d, d == today), 0, i)
            self.grid.setColumnStretch(i, 1)

    def _day(self, d: date, is_today: bool) -> QWidget:
        card = QFrame()
        card.setObjectName("today" if is_today else "day")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        name = _label(S.WEEKDAYS_SHORT[d.weekday()].upper(), "cap", wrap=False)
        if is_today:
            name.setStyleSheet(f"color: {C.PRI};")
        head.addWidget(name)
        head.addWidget(_label(f"{d.day}.{d.month:02d}", "hint", wrap=False))
        head.addStretch(1)
        add = _icon_btn("plus", "Добавить пару", 12)
        add.clicked.connect(lambda _=False, wd=d.weekday(): self.edit_lesson(None, wd))
        head.addWidget(add)
        lay.addLayout(head)
        items = self.st.lessons_on(d)
        for x in items:
            lay.addWidget(self._lesson(x))
        if not items:
            lay.addWidget(_label("свободно", "hint"))
        lay.addStretch(1)
        return card

    def _lesson(self, x: S.Lesson) -> QWidget:
        box = QFrame()
        box.setObjectName("lesson")
        box.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(9, 7, 9, 7)
        lay.setSpacing(1)
        lay.addWidget(_label(x.start + (f"–{x.end}" if x.end else ""), "time", wrap=False))
        lay.addWidget(_label(x.subject, "subj"))
        meta = " · ".join(p for p in (KIND_SHORT.get(x.kind, x.kind), x.room and f"ауд. {x.room}",
                                      {"odd": "нечёт", "even": "чёт"}.get(x.weeks, "")) if p)
        if meta:
            lay.addWidget(_label(meta, "hint"))
        box.mousePressEvent = lambda _e, les=x: self.edit_lesson(les)
        return box

    def edit_lesson(self, lesson: S.Lesson | None, weekday: int = 0):
        dlg = LessonDialog(self, self.st, lesson, weekday)
        if dlg.exec():
            self.render_week()

    # ── задачи ───────────────────────────────────────────────────────────────
    def _tasks_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("canvas")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(12)
        add = QFrame()
        add.setObjectName("ai")
        al = QVBoxLayout(add)
        al.setContentsMargins(16, 12, 16, 12)
        al.setSpacing(8)
        al.addWidget(_label("Новая задача", "h2"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.t_title = QLineEdit(placeholderText="Что сделать — «решить задачи 5–10»")
        self.t_subject = QComboBox()
        self.t_subject.setEditable(True)
        self.t_subject.lineEdit().setPlaceholderText("Предмет")
        self.t_subject.setFixedWidth(200)
        self.t_due = QLineEdit(placeholderText="Срок — «в пятницу», «5.10»")
        self.t_due.setFixedWidth(190)
        self.t_kind = QComboBox()
        self.t_kind.addItems([k.capitalize() for k in S.TASK_KINDS])
        self.t_kind.setFixedWidth(130)
        btn = QPushButton("  Добавить")
        btn.setObjectName("primary")
        btn.setIcon(qicon("plus", 13, C.BG))
        btn.clicked.connect(self.add_task)
        for w in (self.t_title, self.t_subject, self.t_due, self.t_kind, btn):
            row.addWidget(w, 1 if w is self.t_title else 0)
        self.t_title.returnPressed.connect(self.add_task)
        self.t_due.returnPressed.connect(self.add_task)
        al.addLayout(row)
        self.t_msg = _label("Или голосом: «Джарвис, добавь домашку по физике на пятницу», «сделал эссе».", "hint")
        al.addWidget(self.t_msg)
        lay.addWidget(add)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("canvas")
        self.task_box = QVBoxLayout(body)
        self.task_box.setContentsMargins(0, 0, 0, 0)
        self.task_box.setSpacing(10)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        return page

    def add_task(self):
        title = self.t_title.text().strip()
        if not title:
            self.t_msg.setText("Напишите, что нужно сделать.")
            return
        t = self.st.add_task(title, self.t_subject.currentText(), self.t_due.text(),
                             S.TASK_KINDS[self.t_kind.currentIndex()])
        self.t_title.clear()
        self.t_due.clear()
        self.t_msg.setText(f"✓  Записал: {self.st.task_text(t)}" + ("" if t.due or not self.t_due.text()
                                                                     else " (срок не понял)"))
        self.render_tasks()

    def render_tasks(self):
        self.t_subject.clear()
        self.t_subject.addItems([""] + self.st.subjects())
        while self.task_box.count():
            it = self.task_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        today = self.st.now().date()
        groups: dict[str, list] = {"Просрочено": [], "Сегодня": [], "На неделе": [], "Позже": [], "Без срока": []}
        for t in self.st.open_tasks():
            if not t.due:
                groups["Без срока"].append(t)
                continue
            d = date.fromisoformat(t.due)
            key = ("Просрочено" if d < today else "Сегодня" if d == today else
                   "На неделе" if d <= today + timedelta(days=7) else "Позже")
            groups[key].append(t)
        done = [t for t in self.st.tasks if t.done]
        if not any(groups.values()) and not done:
            self.task_box.addWidget(_label("Задач нет. Добавьте первую выше — или скажите Джарвису.", "hint"))
        for name, items in groups.items():
            if items:
                self.task_box.addWidget(_cap(f"{name} · {len(items)}"))
                self.task_box.addWidget(self._task_card(items, today))
        if done:
            toggle = QPushButton(f"  Сделано · {len(done)}")
            toggle.setIcon(qicon("up" if self.show_done else "down", 12, C.TEXT_MED))
            toggle.clicked.connect(self._toggle_done)
            self.task_box.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
            if self.show_done:
                self.task_box.addWidget(self._task_card(done[-20:], today))
        self.task_box.addStretch(1)

    def _toggle_done(self):
        self.show_done = not self.show_done
        self.render_tasks()

    def _task_card(self, items, today: date) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(0)
        for i, t in enumerate(items):
            row = QFrame()
            row.setObjectName("task")
            r = QHBoxLayout(row)
            r.setContentsMargins(0, 9, 0, 9)
            r.setSpacing(12)
            chk = QPushButton()
            chk.setObjectName("checked" if t.done else "check")
            chk.setFixedSize(22, 22)
            if t.done:
                chk.setIcon(qicon("check", 12, C.BG))
            chk.setToolTip("Вернуть" if t.done else "Готово")
            chk.setCursor(Qt.CursorShape.PointingHandCursor)
            chk.clicked.connect(lambda _=False, task=t: self.toggle_task(task))
            r.addWidget(chk)
            col = QVBoxLayout()
            col.setSpacing(1)
            title = _label(t.title, "subj")
            if t.done:
                title.setStyleSheet(f"color: {C.TEXT_DIM}; text-decoration: line-through;")
            col.addWidget(title)
            meta = " · ".join(p for p in (t.subject, t.kind if t.kind != "домашка" else "") if p)
            if meta:
                col.addWidget(_label(meta, "hint"))
            r.addLayout(col, 1)
            if t.due:
                d = date.fromisoformat(t.due)
                due = _label(S.say_date(d, today), "late" if d < today and not t.done else
                             "soon" if d <= today + timedelta(days=1) and not t.done else "hint", wrap=False)
                r.addWidget(due)
            rm = _icon_btn("trash", "Удалить", 13, C.TEXT_DIM)
            rm.clicked.connect(lambda _=False, task=t: self.delete_task(task))
            r.addWidget(rm)
            lay.addWidget(row)
            if i < len(items) - 1:
                lay.addWidget(_line())
        return card

    def toggle_task(self, t: S.Task):
        t.done = not t.done
        self.st.save()
        self.render_tasks()

    def delete_task(self, t: S.Task):
        self.st.tasks = [x for x in self.st.tasks if x.id != t.id]
        self.st.save()
        self.render_tasks()


def open_dialog(parent=None) -> StudyDialog:
    dlg = StudyDialog(parent)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
