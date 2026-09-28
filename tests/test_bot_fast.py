"""Бот в Telegram отвечает без «раздумий» у 2.5-flash; другим моделям параметр не шлём."""
from telegram_bot.gemini_client import GeminiClient


def test_thinking_off_only_where_supported(monkeypatch):
    monkeypatch.delenv("GEMINI_THINKING_BUDGET", raising=False)
    assert GeminiClient._fast("gemini-2.5-flash")["thinking_config"].thinking_budget == 0
    assert GeminiClient._fast("gemini-2.5-flash-lite")["thinking_config"].thinking_budget == 0
    assert GeminiClient._fast("gemini-2.0-flash") == {} and GeminiClient._fast("gemini-flash-latest") == {}
    monkeypatch.setenv("GEMINI_THINKING_BUDGET", "512")
    assert GeminiClient._fast("gemini-2.5-flash")["thinking_config"].thinking_budget == 512
