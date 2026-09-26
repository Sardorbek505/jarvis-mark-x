"""Одна память у голосового Джарвиса на ПК и Telegram-бота.

Сервер — настоящий MemoryStore на SQLite, ПК — настоящие файлы памяти;
между ними вместо сети — прямой вызов apply_sync (как сделал бы HTTP)."""
import asyncio

import pytest

import memory.conversation as conv
import memory.memory_manager as mm
import memory.shared as shared
from telegram_bot import context_builder, shared_memory

UID = 42


def _link(monkeypatch, store, uid=UID, calls=None):
    """ПК «видит» сервер: sync() уходит прямо в apply_sync на настоящей базе.
    sync() синхронный (на ПК он в своём потоке) — зовём его через sync_()."""
    monkeypatch.setattr(shared, "_config", lambda: ("https://bot.example", "секрет"))
    loop = asyncio.get_running_loop()

    def post(url, body, headers):
        assert url == "https://bot.example/api/memory/sync"
        assert headers == {"Authorization": "Bearer секрет"}
        if calls is not None:
            calls.append(body)
        fut = asyncio.run_coroutine_threadsafe(shared_memory.apply_sync(store, uid, body), loop)
        return 200, fut.result(10)
    return post


async def sync_(post):
    return await asyncio.to_thread(shared.sync, post=post)


@pytest.fixture
def server(mem):
    return mem


async def test_fact_said_on_pc_reaches_bot_and_bot_fact_reaches_pc(monkeypatch, server):
    post = _link(monkeypatch, server)
    mm.update_memory({"relationships": {"брат": "Азиз, учится в Ташкенте"}})
    await server.add_fact(UID, "Любит плов по субботам")       # сказал боту
    assert (await sync_(post)).startswith("ок: +1")

    facts = await server.get_facts(UID)
    assert "брат: Азиз, учится в Ташкенте" in facts
    block = server.cached_block(UID)
    assert "Азиз" in block and "плов" in block                        # бот знает оба

    pc = conv.prompt_context(resuming=True)
    assert "Любит плов по субботам" in pc                              # ПК знает факт бота
    assert pc.count("Азиз") == 0                                      # свой факт не дублируется в общем блоке
    assert "плов" in conv.recall("плов")


async def test_forget_on_pc_also_forgets_on_bot(monkeypatch, server):
    post = _link(monkeypatch, server)
    mm.update_memory({"preferences": {"музыка": "Macan"}})
    await server.add_fact(UID, "Слушает Macan в дороге")
    await sync_(post)
    conv.forget_about("Macan")
    await sync_(post)
    facts = await server.get_facts(UID)
    assert not any("macan" in f.lower() for f in facts)
    assert "Macan" not in conv.prompt_context(resuming=True)


async def test_changed_fact_replaces_old_on_bot(monkeypatch, server):
    post = _link(monkeypatch, server)
    mm.update_memory({"identity": {"город": "Ташкент"}})
    await sync_(post)
    conv.apply_extraction({"remove": [{"category": "identity", "key": "город"}],
                           "facts": [{"category": "identity", "key": "город", "value": "Самарканд"}]})
    await sync_(post)
    facts = await server.get_facts(UID)
    assert "город: Самарканд" in facts and "город: Ташкент" not in facts


async def test_voice_talk_reaches_bot_but_not_gemini_history(monkeypatch, server):
    post = _link(monkeypatch, server)
    await server.add_message(UID, "user", "привет из телеграма")
    conv.log_turn("user", "у меня завтра экзамен по физике")
    conv.log_turn("jarvis", "Удачи, сэр.")
    conv.add_episode("Готовились к экзамену по физике.")
    await sync_(post)

    hist = await server.recent_messages(UID, 40)
    assert [m["role"] for m in hist] == ["user"]                       # pc_* не ломают историю Gemini
    cfg = type("C", (), {"default_city": "Ташкент", "timezone": "Asia/Tashkent"})()
    ctx = context_builder.build_context(server, cfg, UID)
    assert "экзамен по физике" in ctx and "голосом на ПК" in ctx
    assert "Готовились к экзамену" in ctx

    # а переписка из Telegram — на ПК
    assert "привет из телеграма" in conv.prompt_context(resuming=True)


async def test_nothing_lost_while_server_is_down(monkeypatch, server):
    good = _link(monkeypatch, server)
    mm.update_memory({"dates": {"день_рождения_мамы": "12 марта"}})

    def down(url, body, headers):
        raise ConnectionError("нет сети")
    assert (await sync_(down)).startswith("нет связи")
    mm.update_memory({"health": {"аллергия": "на орехи"}})              # пока сети нет
    await sync_(good)
    facts = await server.get_facts(UID)
    assert "день рождения мамы: 12 марта" in facts and "аллергия: на орехи" in facts
    await sync_(good)                                            # повтор не дублирует
    assert len(await server.get_facts(UID)) == 2


async def test_only_new_telegram_messages_are_fetched(monkeypatch, server):
    calls = []
    post = _link(monkeypatch, server, calls=calls)
    await server.add_message(UID, "user", "первое")
    await sync_(post)
    await server.add_message(UID, "model", "второе")
    await sync_(post)
    assert calls[1]["since_msg_id"] > 0
    tg = shared._read(shared.SHARED_FILE, {})["telegram"]
    assert [m["text"] for m in tg] == ["первое", "второе"]


def test_not_configured_is_local_only():
    mm.update_memory({"identity": {"имя": "Сардор"}})
    assert shared.sync() == "не настроено"
    assert not shared.OUTBOX_FILE.exists()                            # очередь не копится зря


async def test_endpoint_requires_token(monkeypatch, server):
    from fastapi.testclient import TestClient

    from telegram_bot import miniapp_server as ms
    monkeypatch.setattr(ms, "_memory", server)
    monkeypatch.setattr(ms, "_pc_link_token", lambda: "s3cret")
    monkeypatch.setattr(ms, "_cfg", lambda: type("C", (), {"allowed_user_ids": [UID]})())
    client = TestClient(ms.app)
    assert client.post("/api/memory/sync", json={}).status_code == 403
    assert client.post("/api/memory/sync", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 403
    r = client.post("/api/memory/sync", json={"facts_add": ["имя: Сардор"]},
                    headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and "имя: Сардор" in r.json()["facts"]
