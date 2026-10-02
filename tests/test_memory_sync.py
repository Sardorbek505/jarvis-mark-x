"""Синхронизация памяти ПК ↔ бот: ничего не теряется, забытое не воскресает.

Каждый тест — дефект, воспроизведённый на настоящем MemoryStore (SQLite) и
настоящих файлах памяти ПК; между ними вместо сети — прямой вызов apply_sync."""
import asyncio

import memory.conversation as conv
import memory.memory_manager as mm
import memory.shared as shared
from telegram_bot import shared_memory

UID = 42


def _link(monkeypatch, store, fail_response=False):
    monkeypatch.setattr(shared, "_config", lambda: ("https://bot.example", "t"))
    loop = asyncio.get_running_loop()

    def post(url, body, headers):
        r = asyncio.run_coroutine_threadsafe(shared_memory.apply_sync(store, UID, body), loop).result(20)
        if fail_response:
            raise TimeoutError("ответ потерялся, хотя сервер всё применил")
        return 200, r
    return post


async def sync_(post, all_=False):
    return await asyncio.to_thread(shared.sync_all if all_ else shared.sync, post=post)


async def test_more_than_server_limit_is_not_lost(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    for i in range(500):
        shared.queue_turn("user", f"реплика {i}", 1000.0 + i)
    await sync_(post, all_=True)
    rows = await mem._fetchall("SELECT COUNT(*) FROM messages WHERE role='pc_user'")
    assert rows[0][0] == 500
    assert shared._read(shared.OUTBOX_FILE, {}).get("turns") == []


async def test_remember_then_forget_stays_forgotten(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    await sync_(post)                                        # сервер сказал, что знает ops
    mm.update_memory({"preferences": {"музыка": "Macan"}})
    conv.forget_about("Macan")
    await sync_(post)
    assert not any("Macan" in f for f in await mem.get_facts(UID))


async def test_changed_value_replaces_old_on_server(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    mm.update_memory({"identity": {"город": "Ташкент"}})
    await sync_(post)
    mm.update_memory({"identity": {"город": "Самарканд"}})
    await sync_(post)
    facts = await mem.get_facts(UID)
    assert "город: Самарканд" in facts and "город: Ташкент" not in facts


async def test_deleted_on_phone_is_deleted_on_pc(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    mm.update_memory({"identity": {"возраст": "21", "город": "Ташкент"}})
    await sync_(post)
    await sync_(post)                                        # факты подтверждены сервером
    await mem.del_fact_text(UID, "возраст: 21")
    await sync_(post)
    assert [k for _c, k, _v in mm.all_facts()] == ["город"]


async def test_empty_server_does_not_wipe_pc(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    mm.update_memory({"identity": {f"факт_{i}": str(i) for i in range(10)}})
    await sync_(post)
    await sync_(post)
    for f in await mem.get_facts(UID):                       # база на сервере «потерялась»
        await mem.del_fact_text(UID, f)
    await mem.add_fact(UID, "что-то новое из бота")
    await sync_(post)
    assert len(mm.all_facts()) == 10


async def test_resend_after_lost_response_does_not_duplicate(monkeypatch, mem):
    post = _link(monkeypatch, mem, fail_response=True)
    shared.queue_turn("user", "привет", 1000.0)
    shared.queue_episode("итог", 1000.0)
    await sync_(post)
    await sync_(post)
    rows = await mem._fetchall("SELECT COUNT(*) FROM messages")
    assert rows[0][0] == 2


async def test_similar_name_is_not_a_duplicate(mem):
    await mem.add_fact(UID, "имя: Сардорбек, учится в ТУИТ")
    assert await mem.add_fact(UID, "любимый клуб: Реал")
    assert not await mem.add_fact(UID, "Реал")               # целым словом — уже известно
    await mem.add_fact(UID, "друг: Сардорбек")
    assert await mem.add_fact(UID, "сосед: Сардор")          # «Сардор» ≠ «Сардорбек»


async def test_forget_also_forgets_the_dialog(mem):
    conv.log_turn("user", "мой пароль от вайфая qwerty123")
    mm.update_memory({"notes": {"пароль_вайфай": "qwerty123"}})
    conv.forget_about("qwerty123")
    found = conv.recall("qwerty123")
    assert "Реплики" not in found and "Факты" not in found and "пароль" not in found


async def test_telegram_messages_are_paged_in_order(mem):
    for i in range(45):
        await mem._exec("INSERT INTO messages(user_id, role, text, created_at) VALUES(?,?,?,?)",
                        (UID, "user", f"m{i}", "2026-10-01T10:00:00"))
    first = await mem.messages_after(UID, 0, 30)
    second = await mem.messages_after(UID, first[-1]["id"], 30)
    assert [m["text"] for m in first + second] == [f"m{i}" for i in range(45)]


async def test_facts_learned_before_linking_are_uploaded(monkeypatch, mem):
    monkeypatch.setattr(shared, "_config", lambda: ("", ""))
    mm.update_memory({"identity": {"имя": "Сардор"}})        # связи с ботом ещё нет
    post = _link(monkeypatch, mem)
    await sync_(post, all_=True)
    assert "имя: Сардор" in await mem.get_facts(UID)


async def test_actions_are_remembered_and_shared(monkeypatch, mem):
    post = _link(monkeypatch, mem)
    conv.log_turn("action", "Открываю Spotify — Включил «Blinding Lights»")
    assert "Джарвис сделал: Открываю Spotify" in conv.format_recent()
    assert "Spotify" in conv.recall("Spotify")
    await sync_(post)
    rows = await mem._fetchall("SELECT role, text FROM messages")
    assert rows == [("pc_jarvis", "Сделал: Открываю Spotify — Включил «Blinding Lights»")]
