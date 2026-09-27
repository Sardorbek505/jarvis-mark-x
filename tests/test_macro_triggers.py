"""Свои команды без фразы: по времени (раз в день, без запуска задним числом
и без повтора после перезапуска), при открытии программы, при запуске;
с «переспрашивать» — только вопрос."""
from datetime import datetime

import pytest

from core import macro_triggers as mt
from core import macros as mc


def test_parse_days_and_time():
    assert mt.parse_days("будни") == [0, 1, 2, 3, 4]
    assert mt.parse_days("по выходным") == [5, 6]
    assert mt.parse_days("пн, ср, пятница") == [0, 2, 4]
    assert mt.parse_days("каждый день") == list(range(7))
    assert mt.parse_days([1, "3", "суббота"]) == [1, 3, 5]
    assert mt.parse_at("9") == "09:00" and mt.parse_at("21.30") == "21:30" and mt.parse_at("25:00") is None
    assert mt.clean_when([{"on": "time", "at": "7:05", "days": "будни"}, {"on": "app", "app": " OBS "},
                          {"on": "start"}, {"on": "bogus"}, "мусор", {"on": "time", "at": "x"}]) == [
        {"on": "time", "at": "07:05", "days": [0, 1, 2, 3, 4]}, {"on": "app", "app": "obs"}, {"on": "start"}]
    assert mt.describe_when({"on": "time", "at": "09:00", "days": [0, 1, 2, 3, 4]}) == "по будням в 09:00"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    m = mc.Macros(tmp_path / "macros.json")
    ran, said, logs = [], [], []
    m.run = lambda c, slots=None, wait=False: ran.append(c.name)
    m.say, m.log = said.append, logs.append
    clock = Clock(datetime(2026, 9, 28, 8, 59))           # понедельник
    procs = {"names": {"explorer.exe"}}
    sch = mt.Scheduler(m, now=clock, processes=lambda: set(procs["names"]))
    return m, sch, clock, procs, ran, said, logs


def _add(m, name, when, confirm=False):
    return m.upsert(mc.Command.from_dict({"name": name, "phrases": [], "confirm": confirm, "when": when,
                                          "steps": [{"do": "say", "value": "привет"}]}))


def test_time_fires_once_in_window_and_survives_restart(env, tmp_path):
    m, sch, clock, _p, ran, _s, logs = env
    _add(m, "Утро", [{"on": "time", "at": "9:00", "days": "будни"}])
    sch.tick()
    assert ran == []                                       # 8:59 — рано
    clock.t = datetime(2026, 9, 28, 9, 0, 30)
    sch.tick()
    sch.tick()
    assert ran == ["Утро"] and "по расписанию" in logs[-1]
    # перезапуск в 9:03 — отметка в файле, второй раз не запустится
    again = mc.Macros(tmp_path / "macros.json")
    again.run = lambda c, *a, **k: ran.append("повтор")
    mt.Scheduler(again, now=lambda: datetime(2026, 9, 28, 9, 3)).tick()
    assert ran == ["Утро"]
    # на следующий будний день — снова
    clock.t = datetime(2026, 9, 29, 9, 1)
    sch.tick()
    assert ran == ["Утро", "Утро"]


def test_time_not_fired_late_or_on_wrong_day(env):
    m, sch, clock, _p, ran, *_ = env
    _add(m, "Утро", [{"on": "time", "at": "9:00", "days": "будни"}])
    clock.t = datetime(2026, 9, 28, 11, 0)                  # включили ПК в 11 — задним числом нет
    sch.tick()
    clock.t = datetime(2026, 10, 3, 9, 0)                   # суббота
    sch.tick()
    assert ran == []


def test_disabled_command_never_fires(env):
    m, sch, clock, _p, ran, *_ = env
    c = _add(m, "Утро", [{"on": "time", "at": "9:00"}])
    c.enabled = False
    m.upsert(c)
    clock.t = datetime(2026, 9, 28, 9, 0)
    sch.tick()
    assert ran == []


def test_app_trigger_only_on_new_process(env):
    m, sch, _c, procs, ran, *_ = env
    _add(m, "Стрим", [{"on": "app", "app": "obs"}])
    procs["names"] = {"explorer.exe", "obs64.exe"}          # уже открыт при запуске — не считается
    sch.tick()
    assert ran == []
    procs["names"] = {"explorer.exe"}
    sch.tick()
    procs["names"] = {"explorer.exe", "obs64.exe"}
    sch.tick()
    sch.tick()
    assert ran == ["Стрим"]


def test_start_trigger_and_confirm_asks_instead_of_running(env):
    m, sch, clock, _p, ran, said, _l = env
    _add(m, "Проверка", [{"on": "start"}])
    _add(m, "Выключить всё", [{"on": "time", "at": "9:00"}], confirm=True)
    sch.on_start()
    assert ran == ["Проверка"]
    clock.t = datetime(2026, 9, 28, 9, 0)
    sch.tick()
    assert ran == ["Проверка"] and "«Выключить всё»" in said[-1] and "macro" in said[-1]


def test_voice_create_with_schedule_and_list(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MACROS", str(tmp_path / "m.json"))
    monkeypatch.setattr(mc, "_macros", None)
    res = mc.macro_tool({"action": "create", "name": "Утро", "steps": [{"do": "say", "value": "доброе утро"}],
                         "when": [{"on": "time", "at": "7:30", "days": "будни"}]})
    assert "по будням в 07:30" in res
    c = mc.macros().find("Утро")
    assert c.phrases == [] and c.when[0]["at"] == "07:30"
    assert "по будням в 07:30" in mc.macros().list_text()


def test_ai_can_return_schedule():
    d = mc.parse_ai('{"name": "Стрим", "steps": [{"do": "open_app", "value": "OBS"}], '
                    '"when": [{"on": "app", "app": "obs64.exe"}]}')
    assert d["when"] == [{"on": "app", "app": "obs64.exe"}] and d["phrases"] == []


def test_editor_when_section(tmp_path):
    """Окно: условия запуска — время с днями, программа; без фразы можно, если есть условие."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])      # noqa: F841 — держим, пока живо окно
    import ui_macros
    m = mc.Macros(tmp_path / "macros.json")
    dlg = ui_macros.MacrosDialog(store=m)
    try:
        dlg.name.setText("Утро")
        dlg.add_step({"do": "say", "value": "доброе утро"})
        row = dlg.add_when({"on": "time"})
        row.at.setText("7:30")
        for b in row.days[5:]:
            b.setChecked(False)
        dlg.add_when({"on": "app", "app": "obs"})
        assert dlg.save_command(), dlg.status.text()
        c = m.find("Утро")
        assert c.phrases == [] and c.when == [{"on": "time", "at": "07:30", "days": [0, 1, 2, 3, 4]},
                                              {"on": "app", "app": "obs"}]
        assert "по будням в 07:30" in dlg.status.text()
        dlg.show_command(c.id)
        assert [r.kind for r in dlg.when_rows] == ["time", "app"] and dlg.when_rows[0].at.text() == "07:30"
        for b in dlg.when_rows[0].days:
            b.setChecked(False)
        assert not dlg.save_command() and "день" in dlg.status.text()
        dlg.when_rows[0].at.setText("утром")
        assert not dlg.save_command() and "09:00" in dlg.status.text()
        dlg._remove_when(dlg.when_rows[0])
        dlg._remove_when(dlg.when_rows[0])
        assert not dlg.save_command() and "фразу" in dlg.status.text()
    finally:
        dlg.close()
