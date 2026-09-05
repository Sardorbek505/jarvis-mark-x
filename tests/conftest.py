"""Shared fixtures for the curated unit suite.

Everything here is offline: a real MemoryStore backed by a throwaway SQLite
file in pytest's tmp dir. No Gemini, Telegram, Neon or network.
"""
import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import onnxruntime  # noqa: F401
except Exception:
    pass

from telegram_bot import memory_store  # noqa: E402


@pytest_asyncio.fixture
async def mem(tmp_path, monkeypatch):
    """Fresh SQLite-backed MemoryStore, isolated per test."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(memory_store, "_SQLITE_PATH", tmp_path / "jarvis_test.db")
    store = memory_store.MemoryStore()
    await store.init()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture(autouse=True)
def _isolate_rag_cache():
    """RAG держит векторы в модульном словаре — между тестами он обязан быть пуст.

    Без этого тест с чистой базой видел векторы, оставшиеся от предыдущего:
    ровно та же ловушка, что и в проде при переключении хранилища.
    """
    from telegram_bot import memory_rag
    memory_rag._VECS.clear()
    yield
    memory_rag._VECS.clear()
