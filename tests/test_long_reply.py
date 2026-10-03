"""Ответ длиннее 4096 символов уходит несколькими сообщениями, а не теряется
целиком с «❌ Что-то пошло не так»."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram import Message

from telegram_bot import bot as bot_mod


def test_разрез_по_абзацам_и_без_потерь():
    text = ("Абзац первый. " * 200).strip() + "\n\n" + ("Абзац второй. " * 200).strip()
    parts = bot_mod.split_message(text)
    assert len(parts) == 2
    assert all(len(p) <= bot_mod.TG_TEXT_LIMIT for p in parts)
    assert parts[1].startswith("Абзац второй")


def test_сплошной_текст_режется_по_лимиту():
    parts = bot_mod.split_message("а" * 10000)
    assert [len(p) for p in parts] == [4096, 4096, 1808]


def test_обёртка_стоит_на_reply_text():
    assert getattr(Message.reply_text, "_long_safe", False)


@pytest.mark.asyncio
async def test_длинный_ответ_уходит_частями_кнопки_на_последней():
    sent = []

    async def reply_text(self, text, **kw):
        if len(text) > 4096:
            raise AssertionError("Message is too long")
        sent.append((text, kw.get("reply_markup")))
        return len(sent)

    wrapped = bot_mod._long_safe(reply_text)
    result = await wrapped(object(), "слово " * 2000, reply_markup="кнопки")
    assert len(sent) == 3 and result == 3
    assert [m for _, m in sent] == [None, None, "кнопки"]
    assert "".join(t for t, _ in sent).replace(" ", "") == ("слово" * 2000)

    sent.clear()
    await wrapped(object(), text="коротко")
    assert sent == [("коротко", None)]
