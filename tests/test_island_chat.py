"""Чат прямо в капсуле и выбор, что сделать с брошенным файлом."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui_island as ui  # noqa: E402


def test_chat_question_and_answer_live_in_the_capsule():
    m = ui.IslandModel()
    m.open_chat(now=0.0)
    assert m.mode(False, now=0.0) == "chat"
    assert m.chat_send("какая погода?", now=1.0) is False          # без файла
    assert m.emotion(now=1.1) == "think"                            # ждёт ответа — думает
    m.notify("ДЖАРВИС", "В Шымкенте +18, ясно.", "reply", now=2.0)
    assert m.chat.answer == "В Шымкенте +18, ясно." and not m.chat.waiting
    assert m.banner(now=2.0) is None                                # ответ в чате, а не баннером
    assert m.chat_view(now=2.0 + ui.CHAT_IDLE_SEC + 1) is None      # тишина — чат закрылся сам


def test_first_question_goes_with_the_file():
    m = ui.IslandModel()
    m.file_ready("quote.pdf", now=0.0)
    assert m.mode(False, now=0.0) == "file" and m.upload is None
    m.open_chat("quote.pdf", now=1.0)
    assert m.file_choice is None
    assert m.chat_send("какая сумма?", now=2.0) is True             # первый — вместе с файлом
    assert m.chat_send("а срок?", now=3.0) is False                 # дальше — обычным текстом
    assert m.file_choice_view(now=0.0) is None


def test_file_choice_expires():
    m = ui.IslandModel()
    m.file_ready("a.txt", now=0.0)
    assert m.file_choice_view(now=ui.FILE_CHOICE_SEC + 1) is None


def _island(**kw):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return ui.Island(poll=False, **kw), app


def _settle(app, sec=0.9):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_chat_field_sends_text_and_file_question():
    sent, actions = [], []
    w, app = _island(on_text=sent.append, on_file_action=lambda a, q: actions.append((a, q)))
    try:
        w.set_wanted(True)
        w.open_chat("quote.pdf")
        _settle(app)
        assert w._edit.isVisible()
        w._edit.setText("какая сумма?")
        w._chat_submit()
        assert actions == [("ask", "какая сумма?")] and sent == []
        w._edit.setText("а срок?")
        w._chat_submit()
        assert sent == ["а срок?"]
        w.set_wanted(False)                                         # окно Джарвиса развернули —
        assert w.wanted                                             # чат не пропадает
        w.close_chat()
        _settle(app, 0.3)
        assert not w._edit.isVisible() and w.model.chat is None
    finally:
        w.close()


def test_file_buttons():
    actions = []
    w, app = _island(on_file_action=lambda a, q: actions.append((a, q)))
    try:
        w.set_wanted(True)
        w.file_ready("quote.pdf")
        _settle(app)
        w.repaint()
        assert {"f_ask", "f_sum", "f_cancel"} <= set(w._buttons)

        class Ev:
            def __init__(self, r):
                self._p = r.center()

            def position(self):
                return self._p
        w.mouseReleaseEvent(Ev(w._buttons["f_sum"]))
        assert actions == [("summary", "")] and w.model.file_choice is None
        w.file_ready("quote.pdf")
        _settle(app)
        w.repaint()
        w.mouseReleaseEvent(Ev(w._buttons["f_ask"]))
        assert w.model.chat and w.model.chat.file == "quote.pdf"     # «Спросить» — чат с файлом
    finally:
        w.close()


def test_instruction_carries_question_or_summary():
    from core.dropped_file import Prepared, instruction
    f = Prepared("quote.pdf", "text", text="Итого 1240 €")
    assert "«какая сумма?»" in instruction(f, "какая сумма?")
    assert "перескажи главное" in instruction(f, summary=True)
    assert "спроси, что с ним сделать" in instruction(f)
