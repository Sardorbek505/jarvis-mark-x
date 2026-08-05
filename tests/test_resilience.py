"""A dead database or a dead cloud must never take JARVIS down.

Covers the July-2026 outage: Neon exceeded its compute quota → memory.init()
raised → FastAPI lifespan crashed → the whole Space exited (code 3) → the PC
client retried every 5s for two weeks and wrote 46k identical log lines.
"""
import asyncio
import json

import pytest

from telegram_bot import memory_store, pc_server


# ── memory: degrade instead of dying ──────────────────────────────────────────

@pytest.fixture
def unreachable_pg(tmp_path, monkeypatch):
    """DATABASE_URL that points nowhere — like a quota-blocked Neon."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(memory_store, "_SQLITE_PATH", tmp_path / "fallback.db")


@pytest.mark.asyncio
async def test_init_survives_dead_postgres(unreachable_pg):
    # Arrange
    store = memory_store.MemoryStore()

    # Act — must not raise, whatever Postgres does
    await store.init()

    # Assert
    assert store._backend == "sqlite"
    assert store.degraded is True
    assert store.degraded_reason
    await store.close()


@pytest.mark.asyncio
async def test_degraded_store_still_remembers(unreachable_pg):
    # Arrange
    store = memory_store.MemoryStore()
    await store.init()

    # Act
    added = await store.add_fact(7, "Сардорбек чинит Джарвиса")

    # Assert — the bot keeps working, on the fallback backend
    assert added is True
    assert "Сардорбек чинит Джарвиса" in await store.get_facts(7)
    stats = await store.stats(7)
    assert stats["backend"] == "SQLite"
    assert stats["degraded"] is True
    await store.close()


@pytest.mark.asyncio
async def test_init_survives_both_backends_dead(unreachable_pg, monkeypatch):
    # Arrange — SQLite fails too (read-only disk, locked file, …)
    async def boom(self):
        raise OSError("disk is read-only")
    monkeypatch.setattr(memory_store.MemoryStore, "_connect_sqlite", boom)
    store = memory_store.MemoryStore()

    # Act
    await store.init()

    # Assert — no crash. The session still holds context in the RAM cache…
    assert store._backend == "off"
    await store.add_fact(7, "живёт только до перезапуска")
    assert await store.get_facts(7) == ["живёт только до перезапуска"]
    assert (await store.stats(7))["backend"] == "нет памяти"

    # …but nothing is persisted — a fresh store starts blank.
    reborn = memory_store.MemoryStore()
    await reborn.init()
    assert await reborn.get_facts(7) == []
    await reborn.close()
    await store.close()


@pytest.mark.asyncio
async def test_watch_promotes_back_to_postgres(unreachable_pg, monkeypatch):
    # Arrange — degraded store, Postgres "comes back" on the next attempt
    monkeypatch.setattr(memory_store, "_PG_RETRY_SEC", 0)
    store = memory_store.MemoryStore()
    await store.init()
    assert store.degraded is True

    async def pg_is_back(self):
        self._backend = "pg"
        self.degraded_reason = ""
    monkeypatch.setattr(memory_store.MemoryStore, "_connect_pg", pg_is_back)

    # Act — one watch tick
    task = asyncio.create_task(store.watch())
    for _ in range(20):
        await asyncio.sleep(0)
        if not store.degraded:
            break
    task.cancel()

    # Assert
    assert store.degraded is False
    assert store._backend == "pg"
    await store.close()   # aiosqlite holds a non-daemon thread — always close


@pytest.mark.asyncio
async def test_init_gives_up_on_a_hanging_postgres(unreachable_pg, monkeypatch):
    # Arrange — a suspended serverless DB accepts the socket and never answers,
    # so init() must time out instead of hanging the whole startup.
    async def never_answers(self):
        await asyncio.sleep(3600)
    monkeypatch.setattr(memory_store.MemoryStore, "_connect_pg", never_answers)
    monkeypatch.setattr(memory_store, "_PG_INIT_TIMEOUT", 0.05)
    store = memory_store.MemoryStore()

    # Act
    await asyncio.wait_for(store.init(), timeout=5)

    # Assert
    assert store._backend == "sqlite"
    assert store.degraded is True
    await store.close()


def test_pooler_urls_disable_statement_cache():
    # Arrange / Act / Assert — Supabase transaction pooler needs this or asyncpg
    # breaks with "prepared statement already exists" under PgBouncer.
    pooled = "postgresql://u:p@aws-0-eu.pooler.supabase.com:6543/postgres"
    assert memory_store._pg_pool_kwargs(pooled) == {"statement_cache_size": 0}
    assert memory_store._pg_pool_kwargs("postgresql://u:p@db.neon.tech/main") == {}


# ── pc_server: back off instead of hammering ──────────────────────────────────

@pytest.mark.asyncio
async def test_reconnect_backs_off_and_stops_spamming(monkeypatch, caplog):
    # Arrange — the cloud is down, exactly like the dead HF Space (HTTP 503)
    def always_503(*a, **kw):
        raise OSError("server rejected WebSocket connection: HTTP 503")
    monkeypatch.setattr(pc_server.websockets, "connect", always_503)

    slept: list[float] = []

    async def fake_sleep(sec):
        slept.append(sec)
        if len(slept) >= 60:
            raise asyncio.CancelledError
    monkeypatch.setattr(pc_server.asyncio, "sleep", fake_sleep)

    # Act
    caplog.set_level("WARNING", logger=pc_server.logger.name)
    with pytest.raises(asyncio.CancelledError):
        await pc_server.run_client("wss://dead.example", "tok")

    # Assert — delays grow to the cap, and are never below the floor
    assert slept[0] <= _ceiling(pc_server._RECONNECT_MIN_SEC)
    assert slept[-1] >= pc_server._RECONNECT_MAX_SEC * (1 - pc_server._RECONNECT_JITTER)
    assert all(s <= _ceiling(pc_server._RECONNECT_MAX_SEC) for s in slept)

    # …and 60 failures produce a handful of lines, not 60
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) <= 1 + 60 // pc_server._LOG_EVERY


@pytest.mark.asyncio
async def test_reconnect_delay_resets_after_success(monkeypatch):
    # Arrange — fail hard, then connect, then fail again
    calls = {"n": 0}

    class _OkWs:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] in (1, 2, 3):
            raise OSError("HTTP 503")
        if calls["n"] == 4:
            return _OkWs()
        raise OSError("HTTP 503")

    monkeypatch.setattr(pc_server.websockets, "connect", flaky)
    slept: list[float] = []

    async def fake_sleep(sec):
        slept.append(sec)
        if calls["n"] >= 5:
            raise asyncio.CancelledError
    monkeypatch.setattr(pc_server.asyncio, "sleep", fake_sleep)

    # Act
    with pytest.raises(asyncio.CancelledError):
        await pc_server.run_client("wss://flaky.example", "tok")

    # Assert — the post-reconnect failure waits from the floor again, not the cap
    assert slept[-1] <= _ceiling(pc_server._RECONNECT_MIN_SEC)
    assert slept[-1] < slept[-2]


def _ceiling(base: float) -> float:
    return base * (1 + pc_server._RECONNECT_JITTER) + 1e-9


# ── дубль деплоя не должен отбирать вебхук ────────────────────────────────────

def test_decommissioned_host_is_recognised():
    # Arrange / Act / Assert — инстанс на Render обязан молчать, на HF работать
    from telegram_bot import config
    assert config.is_decommissioned("https://jarvis-mark-x.onrender.com") is True
    assert config.is_decommissioned("https://atabekovch-jarvis-mark-x.hf.space") is False
    assert config.is_decommissioned("") is False


# ── короткий обрыв ПК не должен быть виден пользователю ──────────────────────

@pytest.mark.asyncio
async def test_pc_counts_as_online_while_relinking(monkeypatch):
    # Arrange — ПК был на связи и только что оборвался
    from telegram_bot import pc_bridge
    b = pc_bridge.PCBridge()
    assert b.connected is False           # никогда не подключался
    cid = await b.register(object())
    assert b.connected is True
    await b.unregister(cid)

    # Assert — в окне переподключения всё ещё «онлайн», иначе bot.py уводит
    # команду ПК в Gemini и Джарвис отвечает болтовнёй вместо действия
    assert b.connected is True
    monkeypatch.setattr(pc_bridge, "_RELINK_GRACE_SEC", 0.0)
    assert b.connected is False           # окно вышло — честно офлайн


@pytest.mark.asyncio
async def test_send_waits_for_a_reconnecting_pc():
    # Arrange — ПК оборвался и возвращается через мгновение
    from telegram_bot import pc_bridge
    sent = []

    class _Ws:
        async def send_text(self, payload): sent.append(payload)

    b = pc_bridge.PCBridge()
    cid = await b.register(_Ws())
    await b.unregister(cid)

    async def comes_back():
        await asyncio.sleep(0.05)
        await b.register(_Ws())

    async def answer():
        for _ in range(200):
            await asyncio.sleep(0.01)
            if b._pending:
                rid = next(iter(b._pending))
                await b.handle_message({"type": "response", "req_id": rid, "text": "готово"})
                return

    # Act
    asyncio.create_task(comes_back())
    asyncio.create_task(answer())
    result = await b.send_command("скриншот", user_id=1, timeout=5)

    # Assert — команда дождалась ПК, а не упала в «ПК офлайн»
    assert result == "готово"
    assert sent and json.loads(sent[0])["text"] == "скриншот"


@pytest.mark.asyncio
async def test_send_fails_fast_when_pc_is_really_off():
    # Arrange — ПК никогда не подключался: ждать нечего
    from telegram_bot import pc_bridge
    b = pc_bridge.PCBridge()

    # Act
    started = asyncio.get_event_loop().time()
    result = await b.send_command("скриншот", user_id=1, timeout=5)

    # Assert — мгновенный честный отказ, без томления пользователя
    assert result is None
    assert asyncio.get_event_loop().time() - started < 0.5
