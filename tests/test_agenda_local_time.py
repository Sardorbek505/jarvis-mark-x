"""Сроки задач считаются от местного времени пользователя, а не сервера (UTC)."""
from datetime import datetime, timedelta, timezone

from telegram_bot import agenda

TZ = timezone(timedelta(hours=5))


def test_time_already_passed_locally_goes_to_tomorrow():
    # 10:00 по местному (05:00 UTC): «в 9:00» — это уже завтра.
    now = datetime(2026, 9, 25, 10, 0, tzinfo=TZ)
    due, title = agenda.parse("в 9:00 созвон", now)
    assert due == "2026-09-26T09:00:00"
    assert title == "созвон"


def test_tomorrow_after_local_midnight():
    # 02:00 по местному — на сервере ещё вчера (21:00 UTC).
    now = datetime(2026, 9, 26, 2, 0, tzinfo=TZ)
    due, _ = agenda.parse("завтра в 9:00 отчёт", now)
    assert due.startswith("2026-09-27")


def test_today_and_overdue_use_given_now():
    now = datetime(2026, 9, 25, 23, 30, tzinfo=TZ)
    assert agenda.is_today("2026-09-25T20:00:00", now)
    assert agenda.is_overdue("2026-09-25T20:00:00", now)
    assert not agenda.is_overdue("2026-09-25T23:45:00", now)
    assert agenda.fmt_due("2026-09-26T09:00:00", now) == "завтра в 09:00"
