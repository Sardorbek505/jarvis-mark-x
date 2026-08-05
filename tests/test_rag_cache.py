"""Векторы берутся из памяти, а не из базы на каждом ходу.

retrieve() раньше на КАЖДОЕ сообщение выгружал все эмбеддинги пользователя и
разбирал JSON каждого вектора. Данные между ходами не меняются — только
дописываются, поэтому запрос к базе там лишний и растёт линейно.
"""
import pytest

from telegram_bot import memory_rag


class _Memory:
    def __init__(self):
        self.loads = 0
        self.rows = [{"kind": "fact", "text": "живёт в Шымкенте", "vec": [1.0, 0.0]}]

    async def all_embeddings(self, uid):
        self.loads += 1
        return list(self.rows)

    async def has_embedding(self, uid, text):
        return False

    async def add_embedding(self, uid, kind, text, vec):
        self.rows.append({"kind": kind, "text": text, "vec": vec})


class _Gemini:
    def __init__(self):
        self.embeds = 0

    async def embed(self, text):
        self.embeds += 1
        return [1.0, 0.0]


@pytest.fixture(autouse=True)
def _clean_cache():
    memory_rag._VECS.clear()
    yield
    memory_rag._VECS.clear()


@pytest.mark.asyncio
async def test_database_is_read_once_not_every_turn():
    mem, gem = _Memory(), _Gemini()
    for _ in range(5):
        await memory_rag.retrieve(mem, gem, 1, "где я живу и чем занимаюсь")
    assert mem.loads == 1                    # было бы 5


@pytest.mark.asyncio
async def test_new_embedding_is_visible_immediately():
    mem, gem = _Memory(), _Gemini()
    await memory_rag.retrieve(mem, gem, 1, "где я живу и чем занимаюсь")   # прогрев кэша
    await memory_rag.index(mem, gem, 1, "fact", "работает в магазине ковров")
    hits = await memory_rag.retrieve(mem, gem, 1, "где я живу и чем занимаюсь")
    assert "работает в магазине ковров" in hits


@pytest.mark.asyncio
async def test_forget_clears_the_cache():
    mem, gem = _Memory(), _Gemini()
    await memory_rag.retrieve(mem, gem, 1, "где я живу и чем занимаюсь")
    memory_rag.forget_cached(1)
    await memory_rag.retrieve(mem, gem, 1, "где я живу и чем занимаюсь")
    assert mem.loads == 2                    # после забывания читаем заново


@pytest.mark.asyncio
async def test_acknowledgement_costs_no_embedding_call():
    mem, gem = _Memory(), _Gemini()
    assert await memory_rag.retrieve(mem, gem, 1, "спасибо") == []
    assert gem.embeds == 0


# ── загрузка досье пользователя ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_dossier_loads_in_parallel(monkeypatch):
    """Семь запросов к базе должны идти параллельно, а не в очередь.

    Последовательно это семь round-trip до Neon подряд — и всё на первом
    сообщении после рестарта, где человек ждёт ответа.
    """
    import asyncio as aio
    from telegram_bot.memory_store import MemoryStore

    store = MemoryStore()
    running, peak = 0, 0

    def slow(result):
        async def fn(*a, **kw):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await aio.sleep(0.02)
            running -= 1
            return result
        return fn

    monkeypatch.setattr(store, "_load_profile", slow({}))
    monkeypatch.setattr(store, "_load_facts", slow([]))
    monkeypatch.setattr(store, "_load_tasks", slow([]))
    monkeypatch.setattr(store, "list_contacts", slow([]))
    monkeypatch.setattr(store, "list_schedule", slow([]))
    monkeypatch.setattr(store, "list_projects", slow([]))
    monkeypatch.setattr(store, "list_notes", slow([]))

    started = aio.get_event_loop().time()
    await store.ensure_loaded(7)
    elapsed = aio.get_event_loop().time() - started

    assert peak == 7                      # все семь в воздухе одновременно
    assert elapsed < 0.10                 # последовательно было бы ~0.14
    assert 7 in store._cache
