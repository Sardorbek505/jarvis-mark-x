"""Утренний брифинг: раз в день, после подъёма и до полудня, в первом
разговоре; факты собираются параллельно (упал один источник — остальные
есть), говорится своими словами, лично; новости — по темам из «Обо мне»."""
import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from core import about_me
from core import briefing as B


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setenv("JARVIS_BRIEFING", "1")
    import core.paths
    monkeypatch.setattr(core.paths, "save_api_keys", lambda d: True)


@pytest.mark.parametrize("raw,hour", [("7:30", 7.5), ("в 8", 8.0), ("06.15", 6.25), ("", 6.0), ("25:00", 6.0)])
def test_wake_hour(raw, hour):
    assert B.wake_hour({"wake_time": raw}) == hour


def test_once_a_day_after_wake_before_noon():
    about_me.answer("wake_time", "7:30")
    assert not B.due(datetime(2026, 9, 28, 7, 0))                       # ещё спит
    assert B.due(datetime(2026, 9, 28, 7, 45))
    B.mark_done(datetime(2026, 9, 28, 7, 45))
    assert not B.due(datetime(2026, 9, 28, 9, 0))                       # сегодня уже было
    assert B.due(datetime(2026, 9, 29, 9, 0))
    assert not B.due(datetime(2026, 9, 29, 12, 5))                      # после полудня — не утро


def test_off_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_BRIEFING", "0")
    assert not B.due(datetime(2026, 9, 28, 9, 0))


def test_gather_survives_broken_and_slow_sources():
    def broken():
        raise RuntimeError("нет сети")

    def slow():
        time.sleep(2)
        return "поздно"
    facts = B.gather(datetime(2026, 9, 28, 8), {"погода": lambda: "Ташкент +18, ясно", "дела": broken,
                                                "новости": slow, "часы": lambda: ""}, timeout=0.3)
    assert facts == {"погода": "Ташкент +18, ясно"}


def test_birthday_first():
    about_me.answer("birthday", "28 сентября")
    facts = B.gather(datetime(2026, 9, 28, 8), {"погода": lambda: "+18"})
    assert list(facts) == ["праздник", "погода"] and "ДЕНЬ РОЖДЕНИЯ" in facts["праздник"]
    about_me.answer("birthday", "28.09")
    assert "праздник" in B.gather(datetime(2026, 9, 28, 8), {})
    assert "праздник" not in B.gather(datetime(2026, 9, 29, 8), {})


def test_instruction_is_personal():
    about_me.answer("address_as", "босс")
    about_me.answer("talk_style", "коротко")
    text = B.instruction({"погода": "+18"}, datetime(2026, 9, 28, 8))
    assert text.startswith("[СИСТЕМА: первый разговор сегодня утром")
    assert "«доброе утро»" in text and "«босс»" in text and "коротко" in text and "- погода: +18" in text
    assert "спокойный день" in B.instruction({}, datetime(2026, 9, 28, 8))


def test_news_by_my_topics(monkeypatch):
    import core.news_manager as nm
    monkeypatch.setattr(nm.NewsManager, "__init__", lambda self: None)
    monkeypatch.setattr(nm.NewsManager, "get_personalized_news", lambda self, limit=5: [
        {"title": "Курс доллара вырос"}, {"title": "Футбол: Пахтакор выиграл"},
        {"title": "Новый iPhone представлен", "category": "технологии"}])
    about_me.answer("news", "футбол, технологии")
    assert B._news(about_me.answers()) == "Футбол: Пахтакор выиграл | Новый iPhone представлен"


def test_clock_today():
    from core import clock as ck
    c = ck.clock()
    c.alarms, c.timers = [], []
    c.alarm_set("23:59")
    assert "будильники сегодня: 23:59" in B._clock(datetime.now())


def test_first_morning_talk_triggers_once(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    ui = SimpleNamespace(logs=[], muted=False, on_text_command=None, write_log=lambda t: None,
                         set_state=lambda s: None, lock_on=lambda t: None)
    j = main.Jarvis(ui)
    said = []
    j.speak = said.append
    monkeypatch.setattr(B, "due", lambda: not said and not getattr(B, "_marked", False))
    monkeypatch.setattr(B, "mark_done", lambda now=None: setattr(B, "_marked", True))
    monkeypatch.setattr(B, "gather", lambda: {"погода": "+18"})
    B._marked = False
    j._maybe_briefing()
    j._maybe_briefing()
    for _ in range(100):
        if said:
            break
        time.sleep(0.01)
    assert len(said) == 1 and "- погода: +18" in said[0]
    del B._marked


def test_tool_returns_facts_not_monologue(monkeypatch):
    monkeypatch.setattr(B, "gather", lambda: {"погода": "+18"})
    text = B.briefing_tool({})
    assert text.startswith("пользователь попросил брифинг") and not text.startswith("[")
    assert not B.due(datetime.now().replace(hour=9))                   # сказал сам — утром не повторит
