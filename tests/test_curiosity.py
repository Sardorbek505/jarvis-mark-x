"""Curiosity engine: poses unasked questions, captures answers as facts."""
import pytest

from telegram_bot import curiosity

UID = 555_000_005


async def _ask(mem, uid=UID):
    """Как это делает бот: выбрать вопрос, доставить, только потом пометить."""
    q = await curiosity.next_question(mem, uid)
    if q:
        await curiosity.mark_asked(mem, uid, q)
    return q


@pytest.mark.asyncio
async def test_pose_sets_pending_and_marks_asked(mem):
    q = await _ask(mem)
    assert q
    assert await mem.get_meta(UID, "curio_pending")  # something pending
    done, total = await curiosity.progress(mem, UID)
    assert done == 1 and total == len(curiosity.BANK)


@pytest.mark.asyncio
async def test_save_answer_stores_fact_and_clears_pending(mem):
    await _ask(mem)
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
        q = await _ask(mem)
        await curiosity.save_answer(mem, UID, "ответ")
        assert q["id"] not in seen
        seen.add(q["id"])


@pytest.mark.asyncio
async def test_bank_exhausts_to_none(mem):
    for _ in range(len(curiosity.BANK)):
        await _ask(mem)
        await mem.set_meta(UID, "curio_pending", "")
    assert await curiosity.next_question(mem, UID) is None


# ── недоставленный вопрос не должен отравлять память ─────────────────────────
@pytest.mark.asyncio
async def test_невыбранный_вопрос_не_считается_заданным(mem):
    """Выбор вопроса сам по себе ничего не меняет — состояние пишет mark_asked.

    Раньше pose() сжигал вопрос и ставил pending ДО отправки, поэтому сбой
    сети терял вопрос навсегда и глушил все следующие.
    """
    q = await curiosity.next_question(mem, UID)
    assert q
    assert not await mem.get_meta(UID, "curio_pending")
    done, _ = await curiosity.progress(mem, UID)
    assert done == 0
    # тот же вопрос доступен снова — банк не потерял его
    assert (await curiosity.next_question(mem, UID))["id"] == q["id"]


@pytest.mark.asyncio
async def test_метка_от_старой_версии_не_принимается_за_ответ(mem):
    """В базе мог остаться pending без даты — от версии, писавшей его до отправки.

    Пользователь такой вопрос не видел, и его следующая фраза («включи музыку»)
    не должна лечь в досье как «Родители: включи музыку».
    """
    await mem.set_meta(UID, "curio_pending", "parents")   # без curio_pending_at
    captured = await curiosity.save_answer(mem, UID, "включи музыку")
    assert captured is False
    assert not await mem.get_meta(UID, "curio_pending")   # метку сняли
    facts = await mem.get_facts(UID)
    assert not any("включи музыку" in f for f in facts)


@pytest.mark.asyncio
async def test_доставленный_вопрос_помечается_датой(mem):
    q = await _ask(mem)
    assert await mem.get_meta(UID, "curio_pending") == q["id"]
    assert await mem.get_meta(UID, "curio_pending_at")     # дата проставлена
    await curiosity.save_answer(mem, UID, "21")
    assert not await mem.get_meta(UID, "curio_pending_at")  # и снята после ответа
