"""MemoryStore CRUD + the /forget privacy regression test.

The headline test is `test_clear_wipes_every_user_table` — it populates every
per-user table, calls clear(), and asserts the DB is empty. An earlier bug left
notes/schedule/projects/outbox/contacts behind on /forget; this would catch it.
"""
import re

import pytest

from telegram_bot import memory_store

UID = 555_000_001


# ── the regression guard ────────────────────────────────────────────────────

async def _populate_everything(mem):
    await mem.set_profile_field(UID, "name", "Тестер")
    await mem.add_fact(UID, "любит чай")
    await mem.add_task(UID, "сдать отчёт")
    await mem.add_habit(UID, "пить воду")
    await mem.add_reminder(UID, "позвонить", "2030-01-01T09:00:00+00:00")
    await mem.add_note(UID, "идея для проекта")
    await mem.add_class(UID, 0, "09:00", "Математика", "ауд. 1")
    await mem.upsert_project(UID, "JARVIS", "в работе")
    await mem.queue_outbound(UID, "me", "себе", "привет", False)
    await mem.add_contact(UID, "брат", "@bro", "младший брат")
    await mem.set_meta(UID, "k", "v")


@pytest.mark.asyncio
async def test_clear_wipes_every_user_table(mem):
    await _populate_everything(mem)

    # sanity: data really landed
    assert await mem.get_facts(UID)
    assert await mem.list_notes(UID)
    assert await mem.list_contacts(UID)

    await mem.clear(UID)

    # every per-user table must be empty for this uid
    for tbl in memory_store.USER_TABLES:
        rows = await mem._fetchall(f"SELECT 1 FROM {tbl} WHERE user_id=?", (UID,))
        assert rows == [], f"clear() left rows in {tbl}"

    # habit_checks (keyed by habit_id) must also be gone
    checks = await mem._fetchall("SELECT 1 FROM habit_checks", ())
    assert checks == [], "clear() left orphaned habit_checks"


def test_user_tables_matches_schema():
    """Guard against clear()-drift: every table with a `user_id` column in the
    SQLite schema must be listed in USER_TABLES (and vice-versa)."""
    schema = memory_store._SCHEMA_SQLITE
    with_user_id = set()
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);", schema, re.S):
        name, body = m.group(1), m.group(2)
        if re.search(r"\buser_id\b", body):
            with_user_id.add(name)
    assert with_user_id == set(memory_store.USER_TABLES), (
        f"USER_TABLES out of sync with schema: "
        f"schema-only={with_user_id - set(memory_store.USER_TABLES)}, "
        f"list-only={set(memory_store.USER_TABLES) - with_user_id}"
    )


# ── focused CRUD round-trips ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notes_crud_and_search(mem):
    await mem.add_note(UID, "купить молоко и хлеб")
    await mem.add_note(UID, "идея: голосовой ассистент")
    notes = await mem.list_notes(UID)
    assert len(notes) == 2

    hits = await mem.search_notes(UID, "молоко")
    assert any("молоко" in n["text"] for n in hits)

    await mem.delete_note(UID, notes[0]["id"])
    assert len(await mem.list_notes(UID)) == 1


@pytest.mark.asyncio
async def test_contacts_resolve(mem):
    await mem.add_contact(UID, "брат", "@bro", "младший брат")
    assert await mem.resolve_contact(UID, "брат") == "@bro"
    assert await mem.resolve_contact(UID, "БРАТ") == "@bro"  # case-insensitive
    assert await mem.resolve_contact(UID, "сосед") is None
    assert await mem.del_contact(UID, "брат") is True
    assert await mem.resolve_contact(UID, "брат") is None


@pytest.mark.asyncio
async def test_schedule_for_day(mem):
    await mem.add_class(UID, 0, "09:00", "Математика", "")
    await mem.add_class(UID, 0, "11:00", "Физика", "")
    await mem.add_class(UID, 1, "10:00", "Химия", "")
    monday = await mem.schedule_for_day(UID, 0)
    assert {c["subject"] for c in monday} == {"Математика", "Физика"}
    await mem.clear_schedule(UID)
    assert await mem.list_schedule(UID) == []


@pytest.mark.asyncio
async def test_projects_upsert_updates_status(mem):
    await mem.upsert_project(UID, "JARVIS", "в работе")
    await mem.upsert_project(UID, "JARVIS", "готово")
    projects = await mem.list_projects(UID)
    assert len(projects) == 1
    assert projects[0]["status"] == "готово"


@pytest.mark.asyncio
async def test_facts_dedup(mem):
    assert await mem.add_fact(UID, "любит чай") is True
    assert await mem.add_fact(UID, "любит чай") is False  # duplicate ignored
    assert await mem.get_facts(UID) == ["любит чай"]


@pytest.mark.asyncio
async def test_outbox_queue_and_consume(mem):
    await mem.queue_outbound(UID, "@bro", "брат", "привет", False)
    pending = await mem.pending_outbound(UID)
    assert len(pending) == 1
    await mem.delete_outbound(pending[0]["id"])
    assert await mem.pending_outbound(UID) == []
