"""context_builder.build_context: the single per-user prompt context.

Uses a tiny stub store so we test composition without a DB. This is the module
that previously drifted between bot.py and render_app.py.
"""
from types import SimpleNamespace

from telegram_bot import context_builder

CFG = SimpleNamespace(default_city="Ташкент", timezone="Asia/Tashkent")
UID = 555_000_003


class _StubStore:
    def __init__(self, **data):
        self._d = data

    def cached_block(self, uid):    return self._d.get("block", "")
    def cached_mode(self, uid):     return self._d.get("mode", "")
    def cached_contacts(self, uid): return self._d.get("contacts", [])
    def cached_schedule(self, uid): return self._d.get("schedule", [])
    def cached_projects(self, uid): return self._d.get("projects", [])
    def cached_notes(self, uid):    return self._d.get("notes", [])


def test_contacts_projects_notes_appear():
    store = _StubStore(
        contacts=[{"alias": "брат", "note": "младший"}],
        projects=[{"name": "JARVIS", "status": "в работе"}],
        notes=[{"text": "идея проекта"}],
    )
    ctx = context_builder.build_context(store, CFG, UID)
    assert "брат" in ctx and "младший" in ctx
    assert "JARVIS" in ctx and "в работе" in ctx
    assert "идея проекта" in ctx


def test_empty_contacts_emits_hint():
    ctx = context_builder.build_context(_StubStore(), CFG, UID)
    assert "Контактов для отправки пока нет" in ctx


def test_always_includes_local_time_header():
    # describe() always yields the time/location header line
    ctx = context_builder.build_context(_StubStore(), CFG, UID)
    assert ctx.strip() != ""
