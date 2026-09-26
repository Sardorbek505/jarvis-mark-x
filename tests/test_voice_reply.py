"""Голосовое: служебное указание не должно выглядеть словами пользователя."""

import pytest

from telegram_bot.gemini_client import GeminiClient


@pytest.mark.asyncio
async def test_указание_для_голосового_идёт_в_систему_а_не_в_реплику(monkeypatch):
    client = GeminiClient(api_key="test")
    seen = {}

    async def fake_generate(contents, user_id=None, extra_system=""):
        seen["contents"], seen["system"] = contents, extra_system
        return "Слушаю, сэр."
    monkeypatch.setattr(client, "_generate", fake_generate)

    await client.chat_with_audio(1, b"OggS...")

    user_parts = seen["contents"][-1].parts
    assert all(p.text is None for p in user_parts), "в реплике пользователя есть текст-указание"
    assert "голосовое" in seen["system"]
