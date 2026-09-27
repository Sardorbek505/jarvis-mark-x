"""Свои команды и контакты ПК в пульте Telegram: список кнопками, запуск с
ответом текстом, команда с «переспрашивать» — только после «да», фраза в
бот — своя команда раньше ключевых слов, «напиши маме» — из «Контактов» ПК."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import macros as mc
from telegram_bot import pc_macros


@pytest.fixture
def store():
    m = mc.macros()
    done = []
    m.do = dict(m.do, open_app=lambda s: done.append(("open_app", s["value"])),
                keys=lambda s: done.append(("keys", s["value"])))
    m.upsert(mc.Command.from_dict({"name": "Режим стрима", "phrases": ["включи режим стрима"],
                                   "steps": [{"do": "open_app", "value": "OBS"},
                                             {"do": "say", "value": "Стрим готов"}]}))
    m.upsert(mc.Command.from_dict({"name": "Выключить всё", "phrases": ["выключи всё"], "confirm": True,
                                   "steps": [{"do": "keys", "value": "alt+f4"}],
                                   "when": [{"on": "time", "at": "23:00", "days": "будни"}]}))
    return m, done


def test_list_shows_own_commands_not_packs(store):
    items = pc_macros.list_items()
    assert [i["name"] for i in items] == ["Выключить всё", "Режим стрима"]
    assert items[0]["confirm"] and items[0]["when"] == "по будням в 23:00"
    assert "Режим стрима" in pc_macros.list_text() and "Новая вкладка" not in pc_macros.list_text()


def test_run_waits_and_reports_what_was_said(store):
    _m, done = store
    res = pc_macros.run("Режим стрима")
    assert res["ok"] and done == [("open_app", "OBS")]
    assert "выполнено" in res["text"] and "🗣 Стрим готов" in res["text"]


def test_confirm_command_needs_yes(store):
    _m, done = store
    res = pc_macros.run("Выключить всё")
    assert res["need_confirm"] and not res["ok"] and done == []
    assert pc_macros.run("Выключить всё", confirmed=True)["ok"] and done == [("keys", "alt+f4")]


def test_failed_step_is_reported(store):
    m, _done = store
    m.do["open_app"] = lambda s: (_ for _ in ()).throw(RuntimeError("OBS не установлен"))
    res = pc_macros.run("Режим стрима")
    assert not res["ok"] and "OBS не установлен" in res["text"] and "🗣" not in res["text"]


def test_tool_step_without_voice_jarvis():
    assert "недоступно" in pc_macros._run_tool("eyes", {})


@pytest.mark.asyncio
async def test_phrase_in_bot_runs_own_command_before_keywords(store, monkeypatch):
    """«включи режим стрима» — своя команда, а не музыка по слову «включи»."""
    from telegram_bot import pc_server
    _m, done = store
    res = await pc_server._execute("Включи режим стрима")
    assert "выполнено" in res["text"] and done == [("open_app", "OBS")]
    res = await pc_server._execute("выключи всё")
    assert "точно" in res["text"] and len(done) == 1
    res = await pc_server._execute("точно выключи всё")
    assert "выполнено" in res["text"] and done[-1] == ("keys", "alt+f4")
    assert "Режим стрима" in (await pc_server._execute("мои команды"))["text"]


class _WS:
    def __init__(self):
        self.sent = []

    async def send(self, s):
        self.sent.append(json.loads(s))


@pytest.mark.asyncio
async def test_pc_server_actions_carry_data(store, monkeypatch):
    from core import contacts as ct
    from telegram_bot import pc_server
    ws = _WS()
    await pc_server._handle(ws, {"action": "list_macros", "req_id": "1"})
    assert [i["name"] for i in ws.sent[-1]["data"]["items"]] == ["Выключить всё", "Режим стрима"]
    await pc_server._handle(ws, {"action": "run_macro", "name": "Выключить всё", "req_id": "2"})
    assert ws.sent[-1]["data"]["need_confirm"] and ws.sent[-1]["ok"] is False
    book = ct.contacts().book
    book.upsert(ct.Contact(name="Мама", telegram="@mama_tg", aliases=["мама", "мамочка"]))
    book.upsert(ct.Contact(name="Сосед", telegram="@sosed", can_message=False))
    await pc_server._handle(ws, {"action": "resolve_contact", "alias": "маме", "req_id": "3"})
    assert ws.sent[-1]["ok"] and ws.sent[-1]["data"]["target"] == "@mama_tg"
    await pc_server._handle(ws, {"action": "resolve_contact", "alias": "соседу", "req_id": "4"})
    assert ws.sent[-1]["ok"] is False and "запрещено" in ws.sent[-1]["text"]


@pytest.mark.asyncio
async def test_bridge_send_action_round_trip():
    from telegram_bot.pc_bridge import PCBridge

    class Sock:
        def __init__(self, bridge):
            self.bridge = bridge

        async def send_text(self, s):
            m = json.loads(s)
            assert m["action"] == "run_macro" and m["name"] == "Стрим"
            await self.bridge.handle_message({"type": "response", "req_id": m["req_id"], "text": "ок",
                                              "ok": True, "data": {"need_confirm": False}})

    b = PCBridge()
    b._clients[1] = Sock(b)
    res = await b.send_action("run_macro", 7, timeout=2, name="Стрим")
    assert res == {"text": "ок", "image_b64": None, "ok": True, "data": {"need_confirm": False}}


@pytest.mark.asyncio
async def test_bot_macros_buttons_and_confirm(monkeypatch):
    from telegram_bot import bot as bot_mod
    calls = []

    class Bridge:
        connected = True

        async def send_action(self, action, uid, timeout=25.0, **kw):
            calls.append((action, kw))
            if action == "list_macros":
                return {"text": "", "ok": True, "data": {"items": [{"name": "Выключить всё", "confirm": True}]}}
            if not kw.get("confirmed"):
                return {"text": "Выполнить «Выключить всё»?", "ok": False, "data": {"need_confirm": True}}
            return {"text": "✅ выполнено", "ok": True, "data": {}}

    class Msg:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text, **kw):
            self.replies.append((text, kw.get("reply_markup")))

    class Q:
        def __init__(self, data):
            self.data, self.message, self.answers = data, Msg(), []

        async def answer(self, *a, **k):
            self.answers.append(a)

        async def edit_message_reply_markup(self, **k):
            pass

    monkeypatch.setattr(bot_mod, "bridge", Bridge())
    monkeypatch.setattr(bot_mod, "_is_authorized", lambda u: True)
    msg = Msg()
    upd = type("U", (), {"effective_message": msg, "effective_user": type("X", (), {"id": 1})()})()
    await bot_mod.cmd_macros(upd, None)
    kb = msg.replies[-1][1]
    data = kb.inline_keyboard[0][0].callback_data
    assert kb.inline_keyboard[0][0].text == "🔒 Выключить всё" and len(data.encode()) <= 64
    q = Q(data)
    await bot_mod._on_macro_button(q, 1, data)
    confirm = q.message.replies[-1][1].inline_keyboard[0][0].callback_data
    assert confirm.startswith("macrook:") and calls[-1][1]["confirmed"] is False
    q2 = Q(confirm)
    await bot_mod._on_macro_button(q2, 1, confirm)
    assert calls[-1][1]["confirmed"] is True and q2.message.replies[-1][0] == "✅ выполнено"


@pytest.mark.asyncio
async def test_bot_send_uses_pc_contacts_when_not_whitelisted(monkeypatch):
    """«Напиши маме» из бота: нет в белом списке бота — ищем в «Контактах» ПК."""
    from telegram_bot import bot as bot_mod

    class Memory:
        async def resolve_contact(self, uid, alias):
            return None

    asked = []

    class Bridge:
        connected = True

        async def send_action(self, action, uid, timeout=25.0, **kw):
            asked.append((action, kw))
            return {"ok": True, "text": "", "data": {"target": "@mama_tg", "name": "Мама"}}

    staged = []

    async def dispatch(token, target, alias, message, as_voice, user_id, sent):
        staged.append((target, alias, message))

    class Msg:
        async def reply_text(self, text, **kw):
            return self

    monkeypatch.setattr(bot_mod, "memory", Memory())
    monkeypatch.setattr(bot_mod, "bridge", Bridge())
    monkeypatch.setattr(bot_mod, "_dispatch_outbound", dispatch)
    upd = type("U", (), {"effective_message": Msg()})()
    await bot_mod._apply_send_directives(upd, 1, "Передаю. [[SEND]]маме | text | Задержусь[[/SEND]]")
    for t in list(bot_mod._pending_sends.values()):
        await t
    assert asked == [("resolve_contact", {"alias": "маме"})]
    assert staged == [("@mama_tg", "маме", "Задержусь")]
