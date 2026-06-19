"""Curiosity engine: poses unasked questions, captures answers as facts."""
import pytest

from telegram_bot import curiosity

UID = 555_000_005


@pytest.mark.asyncio
async def test_pose_sets_pending_and_marks_asked(mem):
    q = await curiosity.pose(mem, UID)
    assert q  # a question string
    assert await mem.get_meta(UID, "curio_pending")  # something pending
    done, total = await curiosity.progress(mem, UID)
    assert done == 1 and total == len(curiosity.BANK)


@pytest.mark.asyncio
async def test_save_answer_stores_fact_and_clears_pending(mem):
    await curiosity.pose(mem, UID)
    captured = await curiosity.save_answer(mem, UID, "мне 21 год")
    assert captured is True
    assert not await mem.get_meta(UID, "curio_pending")
    facts = await mem.get_facts(UID)
    assert any("21" in f for f in facts)


@pytest.mark.asyncio
async def test_save_answer_noop_when_nothing_pending(mem):
    assert await curiosity.save_answer(mem, UID, "просто сообщение") is False


@pytest.mark.asyncio
async def test_pose_never_repeats(mem):
    seen = set()
    for _ in range(5):
        q = await curiosity.pose(mem, UID)
        await curiosity.save_answer(mem, UID, "ответ")
        assert q not in seen
        seen.add(q)


@pytest.mark.asyncio
async def test_bank_exhausts_to_none(mem):
    # ask everything
    for _ in range(len(curiosity.BANK)):
        await curiosity.pose(mem, UID)
        await mem.set_meta(UID, "curio_pending", "")
    assert await curiosity.next_question(mem, UID) is None
    assert await curiosity.pose(mem, UID) is None
