"""Будильник — только по просьбе: модель сама поставила «Будильник 07:00»."""
import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture
def jarvis(tmp_path, monkeypatch):
    import main
    from core import clock as ck
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ck, "_clock", ck.Clock(tmp_path / "clock.json"))
    ui = SimpleNamespace(logs=[], muted=False, on_text_command=None)
    ui.write_log = ui.logs.append
    ui.set_state = lambda s: None
    ui.lock_on = lambda t: None
    return main, main.Jarvis(ui), ck


def _set(j, time="07:00"):
    fc = SimpleNamespace(id="1", name="clock", args={"action": "alarm_set", "time": time})
    return asyncio.run(j._execute_tool(fc)).response["result"]


@pytest.mark.parametrize("said", ["", "что ты обо мне знаешь", "включи Lovesong", "я обычно в семь дома"])
def test_no_alarm_without_request(jarvis, said):
    main, j, ck = jarvis
    j.last_user_text = said
    assert "НЕ ВЫПОЛНЕНО" in _set(j)
    assert ck.clock().alarms == []


@pytest.mark.parametrize("said", ["разбуди меня в 7", "поставь будильник на 6:30", "мне надо встать в семь",
                                  "Wake me up at 7", "soat 7 da uyg'ot"])
def test_alarm_when_asked(jarvis, said):
    main, j, ck = jarvis
    j.last_user_text = said
    assert "Будильник на" in _set(j)
    assert len(ck.clock().alarms) == 1


def test_request_still_being_spoken_counts(jarvis):
    """Инструмент зовут посреди хода — реплика ещё в буфере, не в last_user_text."""
    main, j, ck = jarvis
    j.last_user_text = "какая погода"
    j._heard_now = "Джарвис, разбуди меня в 7"
    assert "Будильник на" in _set(j)
