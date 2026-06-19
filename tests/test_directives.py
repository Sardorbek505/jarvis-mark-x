"""directives.apply: hidden [[BLOCK]]s execute against the store and are stripped."""
from datetime import timezone

import pytest

from telegram_bot import directives

UID = 555_000_002
TZ = timezone.utc

REPLY = (
    "Конечно, всё записал.\n"
    "[[REMINDERS]]\n2030-01-01 09:00 | купить хлеб\n[[/REMINDERS]]\n"
    "[[HABITS]]\n- пить воду\n[[/HABITS]]\n"
    "[[TASKS]]\n- сдать отчёт\n[[/TASKS]]\n"
    "[[NOTES]]\n- идея для проекта\n[[/NOTES]]\n"
    "[[SCHEDULE]]\nПн | 09:00 | Математика | ауд. 1\n[[/SCHEDULE]]\n"
    "[[PROJECT]]\nJARVIS | в работе\n[[/PROJECT]]\n"
)


@pytest.mark.asyncio
async def test_apply_executes_and_strips_all_blocks(mem):
    clean, summary = await directives.apply(mem, UID, REPLY, TZ)

    # directive markers fully removed from the visible reply
    assert "[[" not in clean and "]]" not in clean
    assert "всё записал" in clean

    # each block produced a summary line
    joined = " ".join(summary)
    for marker in ("напоминаний", "привычек", "задач", "заметок", "пар", "проектов"):
        assert marker in joined, f"missing summary for {marker}"

    # and the rows actually persisted
    assert any("хлеб" in r["text"] for r in await mem.list_reminders(UID))
    assert await mem.get_tasks(UID)
    assert await mem.list_notes(UID)
    assert await mem.schedule_for_day(UID, 0)
    assert await mem.list_projects(UID)


@pytest.mark.asyncio
async def test_apply_noop_on_plain_text(mem):
    clean, summary = await directives.apply(mem, UID, "просто привет", TZ)
    assert clean == "просто привет"
    assert summary == []
