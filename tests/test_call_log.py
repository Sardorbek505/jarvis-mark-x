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
