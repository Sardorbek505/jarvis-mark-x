"""Учёба, «Обо мне», звонки на телефоне: ПК шлёт снимок с синхронизацией
памяти (только изменившееся), сервер хранит его и считает вкладку тем же
кодом, что на ПК; правка с телефона уходит на ПК и не затирается Джарвисом."""
import json
import time
from datetime import datetime

import pytest

from core import pc_snapshot
from core import study as S
from telegram_bot import pc_views
from telegram_bot import shared_memory

UID = 7


@pytest.fixture(autouse=True)
def _fresh_hashes(monkeypatch):
    monkeypatch.setattr(pc_snapshot, "_sent_hash", {})


def test_snapshot_sends_only_what_changed():
    s = S.study()
    s.lessons.append(S.Lesson("Матан", 0, "08:30", "10:00", room="301"))
    s.save()
    first = pc_snapshot.collect()
    assert set(first) == {"study", "about", "calls", "football"}
    assert first["football"][1]["club"] == ""                   # клуб не указан — без сети, пусто
    assert first["study"][1]["lessons"][0]["subject"] == "Матан"
    pc_snapshot.mark_sent(first)
    assert pc_snapshot.collect() == {}                         # ничего не поменялось — ничего не шлём
    s.add_task("реферат", "матан", "в пятницу")
    again = pc_snapshot.collect()
    assert set(again) == {"study"} and again["study"][1]["tasks"][0]["title"] == "реферат"


def test_phone_edit_is_not_overwritten_by_jarvis_on_pc():
    """Правку с телефона делает pc_server (другой процесс) — Джарвис на ПК
    перечитывает файл, а не затирает его своей старой копией."""
    jarvis = S.study()
    jarvis.add_task("эссе", "", "")
    other_process = S.Study(jarvis.path)                     # как pc_server
    time.sleep(0.01)
    other_process.add_task("лаба", "", "завтра")
    jarvis.tick()                                            # напоминания — с сохранением
    jarvis.add_task("доклад", "", "")
    on_disk = [x["title"] for x in json.loads(jarvis.path.read_text(encoding="utf-8"))["tasks"]]
    assert on_disk == ["эссе", "лаба", "доклад"]


def test_apply_from_phone():
    res = pc_snapshot.apply("study_add", {"title": "реферат", "subject": "", "due": "в пятницу"})
    assert res["ok"] and res["data"]["part"] == "study"
    tid = res["data"]["snapshot"]["tasks"][0]["id"]
    done = pc_snapshot.apply("study_done", {"id": tid})
    assert done["ok"] and "Отметил" in done["text"] and done["data"]["snapshot"]["tasks"][0]["done"]
    assert pc_snapshot.apply("study_done", {"id": "нет"})["ok"] is False
    ans = pc_snapshot.apply("about_answer", {"key": "city", "value": "Ташкент"})
    assert ans["ok"] and any(q["key"] == "city" and q["value"] == "Ташкент"
                             for q in ans["data"]["snapshot"]["questions"])
    assert pc_snapshot.apply("about_answer", {"key": "пароль", "value": "x"})["ok"] is False


def test_study_view_same_rules_as_pc():
    snap = {"semester_start": "2026-09-01", "at": "2026-09-28T08:00:00",
            "lessons": [{"subject": "Матан", "weekday": 0, "start": "08:30", "end": "10:00", "room": "301",
                         "kind": "практика"},
                        {"subject": "Физра", "weekday": 0, "start": "12:00", "weeks": "even"}],
            "tasks": [{"title": "реферат", "due": "2026-09-30", "subject": "Матан"},
                      {"title": "старое", "due": "2026-09-20"}, {"title": "эссе"},
                      {"title": "было", "done": True}]}
    v = pc_views.study_view(snap, datetime(2026, 9, 28, 8, 0))       # понедельник, нечётная неделя
    assert v["has"] and v["parity"] == "нечётная" and "Матан в 08:30" in v["now_next"]
    mon = v["week"][0]
    assert mon["today"] and [x["subject"] for x in mon["lessons"]] == ["Матан"]   # Физра — по чётным
    groups = {t["title"]: t["group"] for t in v["tasks"]}
    assert groups == {"старое": "overdue", "реферат": "week", "эссе": "nodate", "было": "done"}
    assert pc_views.study_view(None, datetime.now()) == {"has": False}


def test_about_and_calls_views():
    about = pc_views.about_view({"groups": ["Кто вы"], "questions": [
        {"key": "name", "label": "Имя", "group": "Кто вы", "value": "Сардор"},
        {"key": "city", "label": "Город", "group": "Кто вы", "value": ""}]})
    assert about["known"] == 1 and about["total"] == 2 and about["groups"][0]["questions"][1]["key"] == "city"
    calls = pc_views.calls_view({"calls": [{"who": "Азиз", "when": "2026-09-28T18:02:11", "sec": 70,
                                            "result": "ок", "lines": [{"who": "Азиз", "text": "иду"}]}]})
    assert calls["calls"][0]["when"] == "2026-09-28 18:02" and calls["calls"][0]["min"] == 1
    assert any(a["title"] == "Учёба" for a in pc_views.abilities())


async def test_sync_stores_snapshots_and_miniapp_shows_study(mem, monkeypatch):
    body = {"snapshots": {"study": {"semester_start": "2026-09-01", "at": "2026-09-28T08:00:00",
                                    "lessons": [{"subject": "Матан", "weekday": 0, "start": "08:30"}], "tasks": []},
                          "bogus": {"x": 1}}}
    await mem.ensure_loaded(UID)
    await shared_memory.apply_sync(mem, UID, body)
    assert (await pc_views.load(mem, UID, "study"))["lessons"][0]["subject"] == "Матан"
    assert await pc_views.load(mem, UID, "bogus") is None

    from telegram_bot import miniapp_server as ms
    monkeypatch.setattr(ms, "_memory", mem)
    monkeypatch.setattr(ms, "_bridge", None)
    view = await ms._build_view(UID, "study")
    assert view["has"] and view["pc_online"] is False and len(view["week"]) == 7
    dash = await ms._build_view(UID, "dashboard")
    assert dash["me"] == {"has": False} and dash["calls"]["calls"] == [] and dash["abilities"]


async def test_phone_edit_goes_to_pc_and_refreshes(mem, monkeypatch):
    from telegram_bot import miniapp_server as ms
    sent, asked = [], []

    class WS:
        async def send_text(self, s):
            sent.append(json.loads(s))

    class Bridge:
        connected = True

        async def send_action(self, action, uid, timeout=25.0, **kw):
            asked.append((action, kw))
            return {"ok": True, "text": "Записал: реферат", "data": {"part": "study", "snapshot": {
                "at": "2026-09-28T09:00:00", "lessons": [], "tasks": [{"title": "реферат", "id": "a1"}]}}}

    monkeypatch.setattr(ms, "_memory", mem)
    monkeypatch.setattr(ms, "_bridge", Bridge())
    await mem.ensure_loaded(UID)
    await ms._pc_edit(WS(), UID, {"type": "study_add", "title": "реферат", "due": "в пятницу"})
    assert asked == [("study_add", {"title": "реферат", "due": "в пятницу"})]
    assert sent[0] == {"type": "pc_edit_result", "ok": True, "text": "Записал: реферат"}
    assert sent[1]["view"] == "study" and sent[1]["payload"]["tasks"][0]["title"] == "реферат"
    monkeypatch.setattr(ms, "_bridge", None)                  # ПК офлайн — честно
    await ms._pc_edit(WS(), UID, {"type": "study_done", "id": "a1"})
    assert sent[2]["ok"] is False and "офлайн" in sent[2]["text"]


async def test_pc_server_routes_phone_edits():
    from telegram_bot import pc_server

    class Sock:
        def __init__(self):
            self.out = []

        async def send(self, s):
            self.out.append(json.loads(s))
    ws = Sock()
    await pc_server._handle(ws, {"action": "study_add", "title": "лаба", "req_id": "1"})
    assert ws.out[-1]["ok"] and ws.out[-1]["data"]["part"] == "study"
