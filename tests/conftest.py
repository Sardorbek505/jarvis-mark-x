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


@pytest.fixture(autouse=True)
def _isolate_voice_memory(tmp_path, monkeypatch):
    """Журнал разговора, итоги и факты голосового Джарвиса — во временную
    папку. Иначе сквозные тесты голосового цикла писали реплики в настоящий
    memory/dialog.jsonl (в режиме разработки папка данных — сам репозиторий)."""
    import memory.conversation as conv
    import memory.memory_manager as mm
    d = tmp_path / "voice_memory"
    # Выученное на голосе владельца слово — не из настоящей папки данных.
    monkeypatch.setenv("JARVIS_WAKE_ALIASES", str(d / "wake_aliases.json"))
    # Готовые фразы — не в настоящий кэш голоса и без озвучки по сети.
    import core.quick as quick
    monkeypatch.setattr(quick, "_cache", quick.VoiceCache(d / "voice_cache"))
    monkeypatch.setenv("JARVIS_QUICK_PREWARM", "0")
    monkeypatch.setenv("JARVIS_KEYS_CHECK", "0")          # проверка ключей при запуске ходит в сеть
    import core.contacts as contacts_mod
    monkeypatch.setenv("JARVIS_CONTACTS", str(d / "contacts.json"))
    monkeypatch.setenv("JARVIS_CONTACTS_LISTEN", "0")
    monkeypatch.setenv("JARVIS_ABOUT_STATE", str(d / "about_me_state.json"))
    monkeypatch.setenv("JARVIS_BRIEFING_STATE", str(d / "briefing_state.json"))
    monkeypatch.setenv("JARVIS_BRIEFING", "0")            # утром сам не срабатывает посреди чужих тестов
    import core.voice_id as voice_id_mod
    monkeypatch.setenv("JARVIS_VOICE_ID", str(d / "voice_id.json"))
    monkeypatch.setattr(voice_id_mod, "_vid", None)
    monkeypatch.setenv("JARVIS_WELCOME_STATE", str(d / "welcome_state.json"))
    monkeypatch.setenv("JARVIS_NO_WELCOME", "1")
    import core.study as study_mod
    monkeypatch.setenv("JARVIS_STUDY", str(d / "study.json"))
    monkeypatch.setattr(study_mod, "_study", None)
    for attr in ("_book", "_me", "_contacts"):
        monkeypatch.setattr(contacts_mod, attr, None)
    # Свои команды — из временного файла, не из настоящей папки данных.
    import core.macros as macros_mod
    monkeypatch.setenv("JARVIS_MACROS", str(d / "macros.json"))
    monkeypatch.setattr(macros_mod, "_macros", None)
    monkeypatch.setattr(mm, "_MEMORY_FILE", d / "data.json")
    monkeypatch.setattr(conv, "DIALOG_FILE", d / "dialog.jsonl")
    monkeypatch.setattr(conv, "EPISODES_FILE", d / "episodes.jsonl")
    monkeypatch.setattr(conv, "STATE_FILE", d / "collector.json")
    monkeypatch.setattr(conv, "_collector", None)
    import memory.shared as shared
    monkeypatch.setattr(shared, "OUTBOX_FILE", d / "sync_outbox.json")
    monkeypatch.setattr(shared, "SHARED_FILE", d / "shared.json")
    monkeypatch.setattr(shared, "_config", lambda: ("", ""))   # без сервера, пока тест не задаст
    yield
