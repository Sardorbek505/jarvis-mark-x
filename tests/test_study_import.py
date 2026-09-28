"""Расписание со скриншота: ответ Gemini разбирается недоверчиво (дни, время,
тип, чётность, номер пары без времени), повторы не дублируются, окно
показывает найденное до сохранения и сохраняет заменой или добавлением."""
import io
import json
import os
import time
from datetime import datetime

import pytest

from core import study as S
from core import study_import as SI

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ANSWER = {"lessons": [
    {"subject": "Математический  анализ", "weekday": 0, "start": "8:30", "end": "09:50", "room": "ауд. 204",
     "teacher": "Иванов И.И.", "kind": "лек", "weeks": "all"},
    {"subject": "Физика", "weekday": "вторник", "start": "10.00", "end": "11.20", "kind": "лаб",
     "weeks": "числитель"},
    {"subject": "Физика", "weekday": 1, "start": "10:00", "end": "11:20", "kind": "лаб", "weeks": "знаменатель"},
    {"subject": "Английский", "weekday": 2, "start": "", "pair": 2, "kind": "пр"},        # только номер пары
    {"subject": "Математический анализ", "weekday": 0, "start": "08:30", "end": "09:50"},  # повтор
    {"subject": "", "weekday": 3, "start": "09:00"},                                     # без предмета
    {"subject": "Физра", "weekday": 6, "start": "09:00"},                                # воскресенье
    {"subject": "Химия", "weekday": 4},                                                  # ни времени, ни пары
    "мусор",
], "note": "одна клетка размыта"}


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (1080, 2400), (20, 30, 40, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_normalize_is_strict():
    res = SI.normalize(ANSWER)
    got = [(x.weekday, x.start, x.end, x.subject, x.kind, x.weeks, x.room) for x in res.lessons]
    assert got == [
        (0, "08:30", "09:50", "Математический анализ", "лекция", "all", "204"),        # без «ауд.»
        (1, "10:00", "11:20", "Физика", "лабораторная", "even", ""),
        (1, "10:00", "11:20", "Физика", "лабораторная", "odd", ""),
        (2, "10:00", "11:20", "Английский", "практика", "all", ""),
    ]
    assert res.guessed_time == 1 and res.note == "одна клетка размыта"
    assert res.lessons[0].teacher == "Иванов И.И."


@pytest.mark.parametrize("text", [
    json.dumps(ANSWER), "```json\n" + json.dumps(ANSWER) + "\n```",
    "Вот расписание: " + json.dumps(ANSWER) + " Готово.", json.dumps(ANSWER["lessons"]),
])
def test_read_accepts_json_in_any_wrapping(text):
    seen = {}

    def ask(jpeg):
        seen["jpeg"] = jpeg
        return text
    res = SI.read(_png(), ask=ask)
    assert len(res.lessons) == 4
    from PIL import Image
    im = Image.open(io.BytesIO(seen["jpeg"]))
    assert im.format == "JPEG" and max(im.size) == SI.MAX_SIDE          # высокий скрин телефона ужат


def test_garbage_answer_gives_nothing():
    assert SI.read(_png(), ask=lambda j: "не могу").lessons == []
    assert SI.normalize({"lessons": "x"}).lessons == []


def test_apply_replace_and_add(tmp_path):
    st = S.Study(tmp_path / "study.json")
    st.lessons = [S.Lesson("История", 3, "09:00")]
    new = SI.normalize(ANSWER).lessons
    assert SI.apply(st, new, replace=False) == 4 and len(st.lessons) == 5
    assert SI.apply(st, SI.normalize(ANSWER).lessons, replace=False) == 0          # повторно — не дублирует
    assert SI.apply(st, new[:2], replace=True) == 2 and len(S.Study(tmp_path / "study.json").lessons) == 2


def _wait(app, cond, sec=5.0):
    end = time.time() + sec
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


@pytest.fixture
def window(tmp_path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui_study
    st = S.Study(tmp_path / "study.json", now=lambda: datetime(2026, 9, 28, 8, 0))
    st.semester_start = "2026-09-01"
    dlg = ui_study.StudyDialog(st=st)
    yield dlg, st, app, ui_study
    dlg.close()


def test_window_imports_screenshot(window):
    dlg, st, app, U = window
    assert "Из фото" in dlg.now_label.text()                                     # подсказка на пустом
    imp = dlg.import_image(_png(), reader=lambda img: SI.read(img, ask=lambda j: json.dumps(ANSWER)))
    assert _wait(app, imp.ok.isEnabled)
    assert "Нашёл пар: 4" in imp.status.text() and imp.warn.isVisibleTo(imp)       # время по звонкам — видно
    assert not imp.mode.isVisibleTo(imp)                                           # было пусто — просто сохранить
    imp._save()
    app.processEvents()
    assert len(st.lessons) == 4 and "Математический анализ" in dlg.now_label.text() + "".join(
        x.subject for x in st.lessons)
    # второй раз — можно добавить к текущему, повторы не множатся
    imp2 = dlg.import_image(_png(), reader=lambda img: SI.read(img, ask=lambda j: json.dumps(ANSWER)))
    assert _wait(app, imp2.ok.isEnabled) and imp2.mode.isVisibleTo(imp2)
    imp2.mode.setCurrentIndex(imp2.mode.findData(False))
    imp2._save()
    assert len(st.lessons) == 4


def test_window_explains_errors(window):
    dlg, st, app, U = window

    def fail(img):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
    imp = dlg.import_image(_png(), reader=fail)
    assert _wait(app, lambda: "Квота" in imp.status.text())
    assert not imp.ok.isEnabled() and st.lessons == []
    imp.reject()
    for exc, word in ((RuntimeError("нет ключа Gemini"), "Ключи"), (TimeoutError("timed out"), "не успел"),
                      (ValueError("что-то"), "ValueError")):
        assert word in U.read_error(exc)


def test_closing_early_is_safe(window):
    dlg, st, app, U = window
    import threading
    go = threading.Event()

    def slow(img):
        go.wait(2)
        return SI.normalize(ANSWER)
    imp = dlg.import_image(_png(), reader=slow)
    imp.reject()
    imp.deleteLater()
    app.processEvents()
    go.set()
    time.sleep(0.1)
    app.processEvents()
    assert st.lessons == []                                    # закрыли — ничего не записалось


def test_paste_from_clipboard(window):
    dlg, st, app, U = window
    from PyQt6.QtGui import QImage
    got = []
    dlg.import_image = lambda data, reader=None: got.append(data)
    img = QImage(40, 30, QImage.Format.Format_RGB32)
    img.fill(0x223344)
    app.clipboard().setImage(img)
    dlg.paste_image()
    if not app.clipboard().image().isNull():                    # в offscreen буфер может не работать
        assert got and got[0][:8] == b"\x89PNG\r\n\x1a\n"
