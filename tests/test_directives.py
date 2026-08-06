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


# ── несколько блоков одного типа ─────────────────────────────────────────────
MULTI = (
    "Записал, сэр.\n"
    "[[TASKS]]\nкупить хлеб\n[[/TASKS]]\n"
    "И по работе тоже:\n"
    "[[TASKS]]\nотправить отчёт\nпозвонить клиенту\n[[/TASKS]]\n"
)


@pytest.mark.asyncio
async def test_исполняются_все_блоки_а_не_первый(mem):
    """Блоки вырезаются через sub (все), а исполнялись через search (один).

    На составную просьбу модель отвечает несколькими блоками одного типа.
    Лишние пропадали молча: не выполнены, но и из текста удалены — следа
    не оставалось ни у пользователя, ни в логе. Ответ «Записал, сэр» при
    одной созданной задаче из трёх.
    """
    clean, summary = await directives.apply(mem, UID, MULTI, TZ)

    titles = " ".join(t["title"] for t in await mem.get_tasks(UID))
    assert "хлеб" in titles
    assert "отчёт" in titles
    assert "клиенту" in titles
    assert "✅ задач: 3" in summary
    assert "[[" not in clean


@pytest.mark.asyncio
async def test_ничего_не_вырезано_мимо_исполнения(mem):
    """Что удалено из видимого текста — то должно быть выполнено."""
    reply = ("Готово.\n"
             "[[NOTES]]\nпервая мысль\n[[/NOTES]]\n"
             "ещё вот:\n"
             "[[NOTES]]\nвторая мысль\n[[/NOTES]]\n")
    clean, _ = await directives.apply(mem, UID, reply, TZ)
    texts = " ".join(n["text"] for n in await mem.list_notes(UID))
    assert "первая мысль" in texts and "вторая мысль" in texts
    assert "мысль" not in clean


@pytest.mark.asyncio
async def test_неразобранное_напоминание_оставляет_след(mem, caplog):
    """Обещанное вслух напоминание не должно исчезать бесследно."""
    reply = "[[REMINDERS]]\nзавтра в 9 - зарядка\n[[/REMINDERS]]"
    await directives.apply(mem, UID, reply, TZ)
    assert any("не разобрана" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_несуществующая_дата_не_молчит(mem, caplog):
    reply = "[[REMINDERS]]\n2026-02-31 09:00 | несуществующий день\n[[/REMINDERS]]"
    await directives.apply(mem, UID, reply, TZ)
    assert not await mem.list_reminders(UID)
    assert any("Несуществующая дата" in r.message for r in caplog.records)
