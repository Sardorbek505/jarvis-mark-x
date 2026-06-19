"""reminders.parse_reminder: natural-language → (when, what)."""
from datetime import datetime, timedelta, timezone

from telegram_bot.reminders import parse_reminder

NOW = datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc)  # Fri 10:00


def test_relative_minutes():
    when, what = parse_reminder("напомни через 30 минут позвонить маме", NOW)
    assert when == NOW + timedelta(minutes=30)
    assert "позвонить маме" in what


def test_tomorrow_absolute():
    when, what = parse_reminder("напомни завтра в 9:00 встреча", NOW)
    assert when.day == 20 and when.hour == 9 and when.minute == 0
    assert "встреча" in what


def test_today_absolute_in_future():
    when, _ = parse_reminder("напомни в 15:00 обед", NOW)
    assert when.day == 19 and when.hour == 15


def test_absolute_already_past_rolls_to_tomorrow():
    when, _ = parse_reminder("напомни в 08:00 зарядка", NOW)
    assert when.day == 20 and when.hour == 8  # 08:00 < 10:00 → next day


def test_half_hour_without_digit():
    when, _ = parse_reminder("напомни через полчаса отдохнуть", NOW)
    assert when == NOW + timedelta(minutes=30)


def test_unparseable_returns_none():
    assert parse_reminder("просто поболтаем о жизни", NOW) is None
