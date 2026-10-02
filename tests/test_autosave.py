"""Единое автосохранение: правка сохраняется сама, без кнопки «Сохранить»,
и не теряется, если уйти с экрана. Раньше в «Ключах», «Контактах», «Своих
командах» и «Футболе» сохранялось только по кнопке."""
import os
import time
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

from core import contacts as CT  # noqa: E402
from core import keys as K  # noqa: E402
from core import macros as mc  # noqa: E402

app = QApplication.instance() or QApplication([])


def wait(ms: int = 800):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def no_save_button(w) -> bool:
    return not any(b.text().strip().startswith("Сохранить") for b in w.findChildren(QPushButton))


def test_autosave_waits_for_pause_and_flushes_on_leave():
    from ui_kit import Autosave, SavedNote
    calls = []
    a = Autosave(None, lambda: calls.append(1), delay=200)
    a.touch()
    a.touch()
    assert calls == []
    wait(400)
    assert calls == [1]                                   # одно сохранение после паузы
    a.touch()
    a.flush()
    a.flush()
    assert calls == [1, 1]                                # уход с экрана — сразу, и только раз
    note = SavedNote()
    note.show_note()
    assert note.text() == "✓ Сохранено"


def test_key_is_saved_without_button(monkeypatch):
    store = {"gemini_api_key": ""}
    monkeypatch.setattr(K, "load_values", lambda: dict(store))
    monkeypatch.setattr(K, "save_values", lambda d: store.update(d) or True)
    monkeypatch.setattr(K, "check", lambda sid, v: ("ok", "Ключ работает."))
    import ui_keys
    dlg = ui_keys.KeysDialog()
    dlg.show()
    try:
        card = dlg.cards["gemini"]
        assert card.save_btn.text() == "Проверить" and no_save_button(dlg)
        card.edits["gemini_api_key"].setText("AIza-test-key")
        wait()
        assert store["gemini_api_key"] == "AIza-test-key"
        assert dlg.saved.text() == "✓ Сохранено"
        for _ in range(100):                              # полный ключ — сразу проверяется
            app.processEvents()
            if card.state == "ok":
                break
            time.sleep(0.01)
        assert card.state == "ok"
        card.edits["gemini_api_key"].setText("AIza-other")  # ушли с экрана, не дождавшись паузы
        dlg.hide()
        assert store["gemini_api_key"] == "AIza-other"
    finally:
        dlg.close()


@pytest.fixture
def book(tmp_path):
    b = CT.Book(tmp_path / "contacts.json", now=lambda: datetime(2026, 9, 27, 14, 0))
    b.upsert(CT.Contact("Мама", "+79991234567", ["мама"]))
    return b


def _contacts(book):
    import ui_contacts

    class Me:
        def linked(self):
            return False

        def run(self, fn, timeout=40):
            raise RuntimeError("нет сети")
    return ui_contacts.ContactsDialog(api=CT.Contacts(book, Me()), caller_ready=lambda: "", open_keys=lambda: None)


def test_contact_saves_itself_when_valid_and_not_before(book):
    dlg = _contacts(book)
    dlg.show()
    try:
        assert no_save_button(dlg)
        dlg.new_contact()
        dlg.name.setText("Бабушка")
        dlg.telegram.setText("бабушка")                   # не username и не номер
        dlg.telegram.editingFinished.emit()
        assert book.one("бабушке")[0] is None and "Telegram" in dlg.status.text()
        dlg.telegram.setText("+998 90 123 45 67")
        dlg.telegram.editingFinished.emit()
        c, _ = book.one("бабушке")
        assert c and c.telegram == "+998901234567" and dlg.saved.text() == "✓ Сохранено"
        dlg.read_aloud.setChecked(True)                   # переключатель — сразу
        assert book.one("бабушке")[0].read_aloud
        dlg.note.setText("глуховата")                     # ушли, не дождавшись паузы
        dlg.hide()
        assert book.one("бабушке")[0].note == "глуховата"
        n = len(book.contacts)
        dlg.show()
        dlg.new_contact()                                 # пустая новая форма — не запись
        dlg.hide()
        assert len(book.contacts) == n
    finally:
        dlg.close()


def test_switching_contact_does_not_resave_unchanged(book, monkeypatch):
    dlg = _contacts(book)
    try:
        saves = []
        monkeypatch.setattr(book, "upsert", lambda c: saves.append(c))
        dlg.show_contact(book.contacts[0].id)
        wait(800)
        assert saves == []                                # заполнение формы — не правка
    finally:
        dlg.close()


def test_command_saves_itself_once_complete(tmp_path):
    m = mc.Macros(tmp_path / "macros.json", foreground=lambda: "chrome.exe")
    import ui_macros
    dlg = ui_macros.MacrosDialog(store=m, build=lambda text: {})
    try:
        assert no_save_button(dlg)
        dlg.new_command()
        dlg.name.setText("Стрим")
        dlg.add_phrase("включи стрим")                    # фраза есть, шагов нет — ещё не команда
        assert not [c for c in m.commands if c.name == "Стрим"] and "шаг" in dlg.status.text()
        row = dlg.add_step({"do": "open_app", "value": "OBS"})
        saved = [c for c in m.commands if c.name == "Стрим"]
        assert saved and saved[0].steps and dlg.saved.text() == "✓ Сохранено"
        row.value.setText("Discord")                      # правка шага — после паузы
        wait()
        assert [c for c in m.commands if c.name == "Стрим"][0].steps[0]["value"] == "Discord"
        dlg.phrase_in.setText("режим стри")               # человек ещё печатает фразу
        dlg.phrase_in.hasFocus = lambda: True
        dlg._touch_now()
        assert dlg.phrase_in.text() == "режим стри"       # недопечатанное не забрали
        assert [c for c in m.commands if c.name == "Стрим"][0].phrases == ["включи стрим"]
    finally:
        dlg.close()


def test_football_club_saves_on_enter_once(monkeypatch, tmp_path):
    import ui_football as U
    from core import football as F
    f = F.Football(tmp_path / "football.json", get_json=lambda *a, **k: {}, get_raw=lambda *a, **k: b"")
    d = U.FootballDialog(None, fb=f, auto_refresh=False)
    try:
        assert no_save_button(d)
        calls = []
        monkeypatch.setattr(d, "_save_club", lambda: calls.append(d.club_edit.text()))
        d.club_edit.setText("Реал")
        d.club_edit.editingFinished.emit()                # Enter
        d.club_edit.editingFinished.emit()                # и уход из поля
        assert calls == ["Реал"]
    finally:
        d.close()
