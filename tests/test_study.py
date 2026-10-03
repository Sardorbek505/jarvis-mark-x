"""Учёба: расписание с чётными/нечётными неделями, «что сейчас / что
завтра», задачи со сроками словами, напоминания (один раз), брифинг."""
from datetime import date, datetime

import pytest

from core import study as S


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def st(tmp_path):
    clk = Clock(datetime(2026, 9, 28, 8, 0))                     # понедельник, 5-я неделя (нечётная)
    s = S.Study(tmp_path / "study.json", now=clk)
    s.semester_start = "2026-09-01"
    s.lessons = [
        S.Lesson("Математический анализ", 0, "08:30", "09:50", "204", kind="лекция"),
        S.Lesson("Физика", 0, "10:00", "11:20", "310", kind="практика"),
        S.Lesson("Английский", 0, "11:30", "12:50", weeks="even"),
        S.Lesson("История", 1, "09:00", "10:20", weeks="odd"),
    ]
    s.save()
    return s, clk


@pytest.mark.parametrize("text,expect", [
    ("завтра", date(2026, 9, 29)), ("послезавтра", date(2026, 9, 30)), ("в пятницу", date(2026, 10, 2)),
    ("до понедельника", date(2026, 10, 5)), ("5 октября", date(2026, 10, 5)), ("до 3 января", date(2027, 1, 3)),
    ("5.10", date(2026, 10, 5)), ("через неделю", date(2026, 10, 5)), ("через 3 дня", date(2026, 10, 1)),
    ("25 сентября", date(2026, 9, 25)), ("20.09", date(2026, 9, 20)),     # недавнее — просрочка, не через год
    ("1 июня", date(2027, 6, 1)), ("5.10.27", date(2027, 10, 5)),
    ("2026-11-01", date(2026, 11, 1)), ("когда-нибудь", None),
])
def test_parse_due(text, expect):
    assert S.parse_due(text, date(2026, 9, 28)) == expect


def test_week_parity_and_day(st):
    s, clk = st
    assert s.week_parity(date(2026, 9, 1)) == "odd" and s.week_parity(date(2026, 9, 7)) == "even"
    assert s.week_parity(date(2026, 9, 28)) == "odd"                          # 5-я неделя
    today = [x.subject for x in s.lessons_on(date(2026, 9, 28))]
    assert today == ["Математический анализ", "Физика"]                        # английский — по чётным
    assert [x.subject for x in s.lessons_on(date(2026, 10, 5))][-1] == "Английский"
    assert s.day_text(date(2026, 9, 28)).startswith("Сегодня 2 пар: 08:30–09:50 Математический анализ ауд. 204")
    assert s.day_text(date(2026, 9, 29)) == "Завтра 1 пар: 09:00–10:20 История."


def test_now_and_next(st):
    s, clk = st
    clk.t = datetime(2026, 9, 28, 9, 0)
    assert s.now_next() == ("Сейчас Математический анализ до 09:50, ауд. 204, "
                            "следующая — Физика в 10:00 (через 60 мин), ауд. 310.")
    clk.t = datetime(2026, 9, 28, 15, 0)
    assert s.now_next() == "Сегодня пар больше нет. Завтра первая — 09:00–10:20 История."


def test_tasks_by_voice(st, monkeypatch):
    s, clk = st
    monkeypatch.setattr(S, "_study", s)
    r = S.study_tool({"action": "add_task", "title": "решить задачи 5-10", "subject": "матан", "due": "в пятницу"})
    assert r == "Записал: Математический анализ: решить задачи 5-10 — пятница."
    S.study_tool({"action": "add_task", "title": "эссе", "subject": "английский", "due": "завтра"})
    S.study_tool({"action": "add_task", "title": "реферат", "subject": "история", "due": "25 сентября",
                  "kind": "проект"})
    text = S.study_tool({"action": "deadlines"})
    assert text.startswith("Просрочено: История: проект реферат — просрочено на 3 дня. "
                           "Скоро: Английский: эссе — завтра; Математический анализ: решить задачи 5-10 — пятница")
    assert S.study_tool({"action": "done", "title": "эссе"}) == "Отметил: Английский: эссе — завтра. Осталось задач: 2."
    assert S.study_tool({"action": "done", "title": "несуществующее"}).startswith("Не нашёл")


def test_reminders_once(st):
    s, clk = st
    notes, said = [], []
    s.notify = lambda title, text: notes.append((title, text))
    s.say = said.append
    s.add_task("лабораторная 3", "Физика", "завтра")
    clk.t = datetime(2026, 9, 28, 8, 22)
    s.tick()
    s.tick()
    assert notes == [("ПАРА", "Через 8 мин — Математический анализ, ауд. 204")]
    clk.t = datetime(2026, 9, 28, 19, 5)
    s.tick()
    assert notes[-1] == ("ДЕДЛАЙН", "Завтра срок: Физика: лабораторная 3") and len(said) == 2
    clk.t = datetime(2026, 9, 29, 8, 5)
    s.tick()
    assert notes[-1] == ("ДЕДЛАЙН", "Сегодня срок: Физика: лабораторная 3")
    again = S.Study(s.path, now=clk)                                            # после перезапуска — не повторяет
    again.notify = lambda title, text: notes.append((title, text))
    n = len(notes)
    again.tick()
    assert len(notes) == n


def test_add_lesson_by_voice_and_briefing(st, monkeypatch):
    s, clk = st
    monkeypatch.setattr(S, "_study", s)
    r = S.study_tool({"action": "add_lesson", "subject": "Программирование", "weekday": "среда", "start": "13:00",
                      "end": "14:20", "room": "105", "kind": "лабораторная"})
    assert r == "Добавил пару: среда, 13:00–14:20 Программирование (лабораторная) ауд. 105."
    assert "Нужны день недели" in S.study_tool({"action": "add_lesson", "subject": "x", "weekday": "?"})
    s.add_task("эссе", "Английский", "послезавтра")
    b = s.briefing()
    assert b.startswith("пары сегодня (2): 08:30–09:50 Математический анализ") and "дедлайны: Английский: эссе" in b
    assert "Программирование" not in b                                        # среда — не сегодня


@pytest.mark.parametrize("said,subject", [
    ("матан", "Математический анализ"), ("линал", "Линейная алгебра"), ("физра", "Физическая культура"),
    ("англ", "Английский"), ("физике", "Физика"), ("история", "История"), ("новый предмет", "новый предмет"),
])
def test_subject_slang(st, said, subject):
    s, _ = st
    s.lessons += [S.Lesson("Линейная алгебра", 2, "08:30"), S.Lesson("Физическая культура", 4, "15:00")]
    assert s.match_subject(said) == subject


def test_focus_uses_clock(tmp_path, monkeypatch):
    from core import clock as ck
    c = ck.Clock(tmp_path / "clock.json")
    monkeypatch.setattr(ck, "_clock", c)
    assert S.focus(25, "физика").startswith("Режим учёбы: 25 минут по физика")
    assert "учёба: физика" in c.timer_list()


def test_empty_schedule_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_study", S.Study(tmp_path / "s.json"))
    assert "окне «Учёба»" in S.study_tool({"action": "tomorrow"})


def test_study_window(st):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    import ui_study
    s, clk = st
    clk.t = datetime(2026, 9, 28, 9, 0)
    dlg = ui_study.StudyDialog(st=s)
    try:
        assert dlg.now_label.text().startswith("Сейчас Математический анализ")
        assert "нечётная" in dlg.week_label.text()
        dlg.shift_week(1)
        assert "Следующая неделя" in dlg.week_label.text() and "чётная" in dlg.week_label.text()
        dlg.show_page(1)
        dlg.t_title.setText("решить задачи 5–10")
        dlg.t_subject.setCurrentText("матан")
        dlg.t_due.setText("в пятницу")
        dlg.add_task()
        t = s.open_tasks()[0]
        assert (t.subject, t.due) == ("Математический анализ", "2026-10-02") and "Записал" in dlg.t_msg.text()
        dlg.toggle_task(t)
        assert s.open_tasks() == [] and s.tasks[0].done
        dlg.delete_task(t)
        assert s.tasks == []
        les = ui_study.LessonDialog(dlg, s, None, 2)
        les.subject.setCurrentText("Программирование")
        les.start.setText("13:00")
        les.end.setText("14:20")
        les._save()
        assert any(x.subject == "Программирование" and x.weekday == 2 for x in s.lessons)
        new = next(x for x in s.lessons if x.subject == "Программирование")
        ui_study.LessonDialog(dlg, s, new)._delete()
        assert all(x.subject != "Программирование" for x in s.lessons)
        dlg.render_week()
        dlg.repaint()
    finally:
        dlg.close()


def test_study_window_validates_input(st):
    """«срок не понял» показывается; пара с концом раньше начала и мусор
    в поле конца не сохраняются."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    import ui_study
    s, _ = st
    dlg = ui_study.StudyDialog(st=s)
    try:
        dlg.show_page(1)
        dlg.t_title.setText("Курсовая")
        dlg.t_due.setText("когда-нибудь потом")
        dlg.add_task()
        assert "срок не понял" in dlg.t_msg.text()

        before = len(s.lessons)
        for end in ("12:00", "abc"):
            les = ui_study.LessonDialog(dlg, s, None, 2)
            les.subject.setCurrentText("Химия")
            les.start.setText("14:00")
            les.end.setText(end)
            les._save()
            assert len(s.lessons) == before, end
            assert les.msg.text()
    finally:
        dlg.close()
