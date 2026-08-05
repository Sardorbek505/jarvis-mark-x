"""Голос: Fish основной, Gemini запасной, ответ не теряется никогда."""
import asyncio

import pytest

from telegram_bot import tts_fish, voice


class _Gemini:
    def __init__(self, result=b"gemini-ogg"):
        self.result, self.calls = result, 0

    async def speak_ogg(self, text):
        self.calls += 1
        return self.result


@pytest.mark.asyncio
async def test_fish_is_used_when_configured(monkeypatch):
    # Arrange
    async def fish_ok(text): return b"OggS" + b"x" * 600
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "speak_ogg", fish_ok)
    g = _Gemini()

    # Act
    out = await voice.speak_ogg("привет", g)

    # Assert — Gemini не тронут, квота сэкономлена
    assert out.startswith(b"OggS")
    assert g.calls == 0


@pytest.mark.asyncio
async def test_falls_back_to_gemini_when_fish_fails(monkeypatch):
    # Arrange — Fish настроен, но упал
    async def fish_dead(text): return None
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "speak_ogg", fish_dead)
    g = _Gemini()

    # Act
    out = await voice.speak_ogg("привет", g)

    # Assert — пользователь всё равно услышал голос
    assert out == b"gemini-ogg" and g.calls == 1


@pytest.mark.asyncio
async def test_gemini_used_when_fish_not_configured(monkeypatch):
    monkeypatch.setattr(tts_fish, "is_configured", lambda: False)
    g = _Gemini()
    assert await voice.speak_ogg("привет", g) == b"gemini-ogg"
    assert g.calls == 1


@pytest.mark.asyncio
async def test_both_dead_returns_none(monkeypatch):
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "speak_ogg", lambda text: asyncio.sleep(0, result=None))
    g = _Gemini(result=None)
    assert await voice.speak_ogg("привет", g) is None


@pytest.mark.asyncio
async def test_fish_rejects_garbage_response(monkeypatch):
    # Arrange — сервис ответил 200, но телом-заглушкой, не Ogg
    monkeypatch.setattr(tts_fish, "_key", lambda: "k")
    monkeypatch.setattr(tts_fish, "_request", lambda text: b"not audio")

    # Act / Assert — битый файл не уедет в Telegram
    assert await tts_fish.speak_ogg("привет") is None


@pytest.mark.asyncio
async def test_empty_text_never_calls_the_api(monkeypatch):
    def boom(text): raise AssertionError("не должно вызываться")
    monkeypatch.setattr(tts_fish, "_key", lambda: "k")
    monkeypatch.setattr(tts_fish, "_request", boom)
    assert await tts_fish.speak_ogg("   ") is None
