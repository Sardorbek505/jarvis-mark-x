"""«Обо мне»: ответы — в общую память, город и имя — туда, где их ждут
(погода, звонки), знакомство голосом по одному вопросу без навязчивости,
окно с анкетой и «что Джарвис запомнил сам»."""
import os
from datetime import datetime

import pytest

from core import about_me as AB


@pytest.fixture
def keys(monkeypatch):
    saved = {}
    import core.paths
    monkeypatch.setattr(core.paths, "save_api_keys", lambda d: saved.update(d) or True)
    return saved


def test_answers_go_to_shared_memory_and_where_needed(keys):
    from memory.memory_manager import load_memory
    assert AB.answer("city", "  Ташкент ") == "Запомнил."
    assert load_memory()["identity"]["city"]["value"] == "Ташкент"
    assert keys["home_city"] == "Ташкент"                               # погоде и «где я»
    AB.answer("name", "Сардор")
    assert keys["user_name"] == "Сардор"
    AB.answer("address_as", "сэр")
    assert keys["user_name"] == "сэр"                                   # звонки: «Здравствуйте, сэр»
    assert AB.answers()["city"] == "Ташкент" and AB.progress() == (3, len(AB.QUESTIONS))
    AB.answer("city", "")                                               # пусто — забыть
    assert AB.answers()["city"] == ""


def test_interview_one_question_at_a_time(keys):
    first = AB.about_tool({"action": "next"})
    assert 'key="name"' in first and "Как вас зовут?" in first
    r = AB.about_tool({"action": "answer", "key": "name", "value": "Сардор"})
    assert r.startswith("Запомнил. Следующий вопрос — key=\"address_as\"")
    r = AB.about_tool({"action": "skip", "key": "address_as"})
    assert 'key="city"' in r                                            # пропущенный — не повторяем
    for q in AB.QUESTIONS[2:]:
        r = AB.about_tool({"action": "answer", "key": q.key, "value": "x"})
    assert "Вопросы закончились" in r and AB.state()["intro"] == "done"
    assert AB.about_tool({"action": "restart"}).startswith("Начинаем. Следующий вопрос — key=\"address_as\"")


def test_offer_is_not_pushy(keys):
    day = datetime(2026, 9, 27, 10)
    assert AB.should_offer(day)
    AB.mark_offered(day)
    assert not AB.should_offer(day)                                     # не чаще раза в день
    assert AB.should_offer(datetime(2026, 9, 28, 10))
    AB.about_tool({"action": "later"})
    assert not AB.should_offer(datetime(2026, 9, 29, 10))               # «потом» — сам больше не предлагает
    st = AB.state()
    st["intro"] = ""
    AB._save_state(st)
    for q in AB.QUESTIONS[:len(AB.QUESTIONS) // 2 + 1]:
        AB.answer(q.key, "x")
    assert not AB.should_offer(datetime(2026, 9, 30, 10))               # уже знакомы — незачем


def test_offers_limited(keys):
    for d in range(AB.MAX_AUTO_OFFERS):
        AB.mark_offered(datetime(2026, 10, 1 + d))
    assert not AB.should_offer(datetime(2026, 10, 20))


def test_intro_instruction_and_status(keys):
    text = AB.intro_instruction()
    assert text.startswith("[СИСТЕМА:") and 'about_me action="next"' in text and "удобно ли сейчас" in text
    AB.answer("music", "рэп")
    assert "Музыка: рэп" in AB.about_tool({"action": "status"})


def test_other_facts_exclude_questionnaire(keys):
    from memory.memory_manager import update_memory
    AB.answer("city", "Ташкент")
    update_memory({"preferences": {"любимый_фильм": "Железный человек"}})
    assert AB.other_facts() == [("preferences", "любимый_фильм", "Железный человек")]


def test_about_window(keys):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    from memory.memory_manager import update_memory
    update_memory({"projects": {"jarvis": "ассистент"}})
    import ui_about
    started = []
    dlg = ui_about.AboutDialog(start_voice=lambda: started.append(1))
    try:
        assert "0 из" in dlg.summary.text()
        e = dlg.edits["city"]
        e.setText("Самарканд")
        e.editingFinished.emit()
        assert AB.answers()["city"] == "Самарканд" and keys["home_city"] == "Самарканд"
        assert "1 из" in dlg.summary.text() and "Самарканд" in dlg.status.text()
        dlg.edits["music"].setText("lo-fi")
        dlg._voice()                                                    # несохранённое — сохраняет и зовёт
        assert AB.answers()["music"] == "lo-fi" and started == [1]
        dlg.forget_fact("projects", "jarvis")
        assert AB.other_facts() == []
        dlg.repaint()
    finally:
        dlg.close()
