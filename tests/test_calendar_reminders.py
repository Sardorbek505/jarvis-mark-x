"""Напоминания календаря: разбор времени и срабатывание.

Раньше «напомни через десять минут» записывалось на сегодня 09:00 (в прошлое),
а записанные напоминания не читал никто — Джарвис отвечал «Добавил» и молчал.
"""
from datetime import datetime, timedelta

import pytest

from core import calendar_manager
from core.calendar_manager import _parse_datetime, add_reminder, take_due_reminders

# Пятница, 21:35
NOW = datetime(2026, 10, 2, 21, 35)


@pytest.mark.parametrize("text, expected", [
    ("через 10 минут", NOW + timedelta(minutes=10)),
    ("через десять минут", NOW + timedelta(minutes=10)),
    ("через двадцать пять минут", NOW + timedelta(minutes=25)),
    ("через час", NOW + timedelta(hours=1)),
    ("через полчаса", NOW + timedelta(minutes=30)),
    ("через полтора часа", NOW + timedelta(minutes=90)),
    ("через 15 мин", NOW + timedelta(minutes=15)),
    ("через 2 дня", NOW + timedelta(days=2)),
    ("в 7 вечера", datetime(2026, 10, 3, 19, 0)),       # сегодня уже прошло — завтра
    ("в 9 утра", datetime(2026, 10, 3, 9, 0)),
    ("в 16", datetime(2026, 10, 3, 16, 0)),
    ("в 12 ночи", datetime(2026, 10, 3, 0, 0)),
    ("в 7:30 вечера", datetime(2026, 10, 3, 19, 30)),
    ("сегодня в 23:00", datetime(2026, 10, 2, 23, 0)),
    ("завтра в 14:00", datetime(2026, 10, 3, 14, 0)),
    ("послезавтра утром", datetime(2026, 10, 4, 9, 0)),
    ("в среду", datetime(2026, 10, 7, 9, 0)),
    ("в субботу в 9.30", datetime(2026, 10, 3, 9, 30)),
    ("в пятницу в 10:00", datetime(2026, 10, 9, 10, 0)),  # сегодня пятница, 10:00 прошло
    ("2026-10-03T19:00:00", datetime(2026, 10, 3, 19, 0)),
])
def test_parse_datetime(text, expected):
    assert _parse_datetime(text, NOW) == expected


def test_parsed_time_is_never_in_the_past():
    for text in ("в 9 утра", "в 7 вечера", "утром", "вечером", "в 21:00"):
        assert _parse_datetime(text, NOW) > NOW, text


@pytest.fixture
def calendar_file(tmp_path, monkeypatch):
    path = tmp_path / "calendar.json"
    monkeypatch.setattr(calendar_manager, "CALENDAR_FILE", path)
    return path


def test_due_reminder_fires_once(calendar_file):
    assert "Добавил напоминание" in add_reminder("позвонить маме", "через 10 минут")

    assert take_due_reminders(datetime.now() + timedelta(minutes=5)) == []
    due = take_due_reminders(datetime.now() + timedelta(minutes=11))
    assert [r["text"] for r in due] == ["позвонить маме"]
    assert due[0]["late"] is False
    # Уже прозвучало — второй раз не повторяется.
    assert take_due_reminders(datetime.now() + timedelta(minutes=12)) == []


def test_missed_reminder_is_marked_late(calendar_file):
    add_reminder("выпить таблетку", "через 10 минут")
    due = take_due_reminders(datetime.now() + timedelta(hours=3))
    assert due[0]["late"] is True
