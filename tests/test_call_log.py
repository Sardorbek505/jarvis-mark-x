"""История звонков: кусочки живой расшифровки склеиваются в реплики, звонок
сохраняется с итогом, «что он сказал?» отвечает расшифровкой, в «Диалог»
расшифровка уходит сразу после звонка."""
import time

from core import call_log as CL
from core import tg_call


def test_merge_glues_pieces_into_lines():
    raw = ["Джарвис: Здравствуйте, Азиз!", "Джарвис: Сардор просил передать", "Вы:При", "Вы:вет", "Вы: , иду",
           "Вы: уже.", "Джарвис: Хорошо, передам.", "Вы:   "]
    assert CL.merge(raw, "Азиз") == [
        {"who": "Джарвис", "text": "Здравствуйте, Азиз! Сардор просил передать"},
        {"who": "Азиз", "text": "Привет, иду уже."},
        {"who": "Джарвис", "text": "Хорошо, передам."}]


def test_calls_are_saved_and_found(tmp_path):
    log = CL.CallLog(tmp_path / "calls.json")
    shown = []
    log.on_added = shown.append
    t0 = time.time() - 125
    log.add("вам", "утренний отчёт", "Поговорили 2 мин, попрощались.", ["Джарвис: Доброе утро", "Вы: привет"], t0)
    log.add("Азиз", "ужин готов", "Поговорили 1 мин.", ["Джарвис: Ужин готов", "Вы: иду"], time.time() - 40)
    assert [e["who"] for e in shown] == ["вам", "Азиз"]
    again = CL.CallLog(tmp_path / "calls.json")                  # пережило перезапуск
    assert again.find("")["who"] == "Азиз" and again.find("азизу")["who"] == "Азиз"
    assert again.find("")["lines"][-1] == {"who": "Азиз", "text": "иду"}
    first = again.find("вам")
    assert first["lines"][1] == {"who": "Вы", "text": "привет"} and first["sec"] >= 120
    assert "Звонок вам" in CL.title(first) and "2 мин" in CL.title(first)
    for i in range(CL.MAX_CALLS + 5):
        again.add(f"Кто{i}", "", "", [], time.time())
    assert len(CL.CallLog(tmp_path / "calls.json").calls) == CL.MAX_CALLS


def test_voice_what_did_he_say():
    assert tg_call.phone_call({"action": "transcript"}) == "Звонков ещё не было."
    CL.call_log().add("Азиз", "ужин", "Поговорили 1 мин.", ["Джарвис: Ужин готов", "Вы: Иду, буду через 10 минут"],
                      time.time())
    res = tg_call.phone_call({"action": "transcript", "name": "Азиз"})
    assert "Азиз: Иду, буду через 10 минут" in res and "Джарвис: Ужин готов" in res
    assert "нет" in tg_call.phone_call({"action": "transcript", "name": "Бобур"})
    assert "Последние звонки" in tg_call.phone_call({"action": "history"})


def test_dialog_panel_shows_call_lines():
    """Строки CALL: рисуются в «Диалоге» как реплики звонка, не как системные."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])      # noqa: F841
    import ui
    w = ui.LogWidget()
    w._handle_append("CALL: Азиз: Иду, буду через 10 минут")
    text = w.toPlainText()
    assert "📞 АЗИЗ" in text and "Иду, буду через 10 минут" in text and "CALL" not in text


# ── расписание и история звонков — разные файлы ────────────────────────────────
def test_schedule_and_history_do_not_corrupt_each_other(tmp_path, monkeypatch):
    import json

    from core import call_log as CL
    from core import tg_call as T
    monkeypatch.setattr("core.paths.get_data_root", lambda: tmp_path)
    monkeypatch.delenv("JARVIS_CALL_LOG", raising=False)
    # Старый общий calls.json с расписанием (списком) — переносится в call_schedule.json.
    (tmp_path / "calls.json").write_text(json.dumps(
        [{"id": 1, "time": "06:00", "repeat": "daily", "date": "2026-10-01", "topic": "отчёт", "last": ""}]),
        encoding="utf-8")
    monkeypatch.setattr(T, "_schedule", None)
    monkeypatch.setattr(CL, "_log", None)
    sch = T.schedule()
    assert [i["time"] for i in sch.items] == ["06:00"]
    log = CL.call_log()
    assert log.calls == []                                    # список — не история, но и не падение
    log.add("Мама", "ужин", "Сказала: приду в семь", ["Вы: при", "Вы: ду в семь"], started=1_700_000_000)
    sch.add("07:30", "напомнить про зал")
    # Перезапуск: обе стороны читаются, ничего не потерялось.
    monkeypatch.setattr(T, "_schedule", None)
    monkeypatch.setattr(CL, "_log", None)
    assert {i["time"] for i in T.schedule().items} == {"06:00", "07:30"}
    assert [c["who"] for c in CL.call_log().calls] == ["Мама"]
    assert T.schedule().describe()                            # раньше — TypeError


def test_what_they_said_glues_word_pieces():
    from core.tg_call import _what_they_said
    # Формат как в TgCall: f"{who}:{кусок}" — пробел в начале куска = новое слово.
    said = _what_they_said(["Вы:При", "Вы:вет, Джар", "Вы:вис", "Джарвис: Здравствуйте", "Вы: я при", "Вы:ду в семь"])
    assert said.startswith("Привет, Джарвис") and "приду в семь" in said


# ── звонок по заданию: разговор, вопросы, язык собеседника ─────────────────────
def test_contact_call_is_a_conversation_with_questions():
    from core.tg_call import instruction_contact
    p = instruction_contact("Сардор", "Ибрагим", "ужин в семь", ask="придёт ли он", note="друг детства")
    assert "ужин в семь" in p and "придёт ли он" in p and "друг детства" in p
    assert "узбекский" in p and "не обещай" in p                    # язык собеседника; без обещаний
    assert "ничего не добавляя от себя" not in p                    # больше не автоответчик


def test_contacts_call_passes_question_and_note(monkeypatch):
    from core import contacts as CT
    api = CT.Contacts.__new__(CT.Contacts)
    got = {}
    c = CT.Contact(name="Ибрагим", telegram="@ibr", note="друг детства")
    monkeypatch.setattr(api, "precheck", lambda *a, **k: (c, ""), raising=False)
    api.me = type("Me", (), {"linked": lambda self: False})()
    api.log = lambda s: None
    api.call_fn = lambda target, name, text, **kw: got.update(kw, text=text) or "Поговорили"
    import threading
    done = threading.Event()
    api.call("Ибрагим", "ужин в семь", done=lambda r: done.set(), ask="во сколько придёт")
    assert done.wait(3) and got == {"ask": "во сколько придёт", "note": "друг детства", "text": "ужин в семь"}


def test_notify_owner_without_link_is_quiet():
    from telegram_bot import pc_server
    assert pc_server.notify_owner("📞 тест") is False
