"""recall.search: in-chat note/fact lookup without /findnote (item #8)."""
import pytest

from telegram_bot import recall

UID = 555_000_004


def test_looks_like_recall_detects_intent():
    assert recall.looks_like_recall("где я записывал про пароль от вайфая")
    assert recall.looks_like_recall("помнишь что я говорил про отпуск?")
    assert not recall.looks_like_recall("давай закажем пиццу")


@pytest.mark.asyncio
async def test_search_finds_matching_note(mem):
    await mem.add_note(UID, "пароль от вайфая дома: hunter2")
    await mem.add_note(UID, "купить подарок маме")
    out = await recall.search(mem, UID, "где я записывал пароль от вайфая?")
    assert "hunter2" in out
    assert "подарок" not in out  # unrelated note not pulled in


@pytest.mark.asyncio
async def test_search_reports_nothing_found(mem):
    await mem.add_note(UID, "купить молоко")
    out = await recall.search(mem, UID, "что я говорил про криптовалюту?")
    assert "ничего не найдено" in out.lower()


@pytest.mark.asyncio
async def test_search_skips_non_recall_messages(mem):
    await mem.add_note(UID, "пароль от вайфая: hunter2")
    out = await recall.search(mem, UID, "закажи пиццу пожалуйста")
    assert out == ""
