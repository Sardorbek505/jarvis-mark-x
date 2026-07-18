"""Умные напоминания: структурированный доступ к событиям + проводка в ProactiveEngine.

До этих тестов _get_smart_reminder_suggestions был заглушкой: он звал
get_events("today"), который отдаёт текст для озвучки, а не данные, и поэтому
никогда не вызывал generate_smart_reminder. Тесты фиксируют, что движок
действительно подключён — напоминание называет событие, а не общую фразу.
"""
import json
from datetime import datetime, timedelta

import pytest

from core import calendar_manager
from core.proactive_engine import ProactiveEngine


def _event(title: str, minutes_from_now: int, **extra) -> dict:
    start = datetime.now() + timedelta(minutes=minutes_from_now)
    return {
        "id": f"event_{title}",
        "title": title,
        "start": start.isoformat(),
        "end": (start + timedelta(hours=1)).isoformat(),
        "duration_minutes": 60,
        "description": extra.get("description", ""),
        "location": extra.get("location", ""),
    }


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    """Изолированный календарь: пишем события, читаем через публичный API."""
    path = tmp_path / "calendar.json"
    monkeypatch.setattr(calendar_manager, "CALENDAR_FILE", path)

    def write(*events):
        path.write_text(
            json.dumps({"events": list(events), "reminders": [], "recurring": []}),
            encoding="utf-8",
        )

    return write


@pytest.fixture
def engine(tmp_path):
    """ProactiveEngine с изолированными паттернами (не трогает config проекта)."""
    return ProactiveEngine(base_dir=tmp_path)


# ─── get_upcoming_events: структурированный доступ ────────────────────────────
def test_returns_event_inside_window(calendar):
    calendar(_event("встреча с Димой", 30))
    events = calendar_manager.get_upcoming_events(minutes_ahead=60)
    assert len(events) == 1
    assert events[0]["title"] == "встреча с Димой"


def test_skips_event_already_past(calendar):
    calendar(_event("прошедшая встреча", -30))
    assert calendar_manager.get_upcoming_events(minutes_ahead=60) == []


def test_skips_event_beyond_window(calendar):
    calendar(_event("встреча завтра", 60 * 24))
    assert calendar_manager.get_upcoming_events(minutes_ahead=60) == []


def test_empty_calendar_returns_empty_list(calendar):
    calendar()
    assert calendar_manager.get_upcoming_events(minutes_ahead=60) == []


def test_missing_calendar_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_manager, "CALENDAR_FILE", tmp_path / "нет-файла.json")
    assert calendar_manager.get_upcoming_events(minutes_ahead=60) == []


def test_broken_event_does_not_kill_the_batch(calendar):
    """Одно битое событие не должно уносить с собой остальные."""
    calendar({"title": "битое", "start": "не-дата"}, _event("нормальное", 30))
    events = calendar_manager.get_upcoming_events(minutes_ahead=60)
    assert [e["title"] for e in events] == ["нормальное"]


def test_events_sorted_by_time(calendar):
    calendar(_event("позже", 50), _event("раньше", 10))
    events = calendar_manager.get_upcoming_events(minutes_ahead=60)
    assert [e["title"] for e in events] == ["раньше", "позже"]


# ─── Проводка: движок реально вызывается ──────────────────────────────────────
def test_suggestion_names_the_actual_event(calendar, engine):
    """Главный тест: заглушка выдавала общую фразу, движок называет событие."""
    calendar(_event("созвон с командой", 30))
    out = engine._get_smart_reminder_suggestions({"last_emotion": "neutral", "mode": "normal"})

    assert any("созвон с командой" in s for s in out), out


def test_suggestion_states_the_time(calendar, engine):
    when = datetime.now() + timedelta(minutes=30)
    calendar(_event("созвон с командой", 30))
    out = engine._get_smart_reminder_suggestions({"last_emotion": "neutral", "mode": "normal"})

    assert any(when.strftime("%H:%M") in s for s in out), out


def test_no_events_means_no_suggestions(calendar, engine):
    calendar()
    assert engine._get_smart_reminder_suggestions({"last_emotion": "neutral"}) == []


def test_overwhelmed_defers_non_critical(calendar, engine):
    """В сильном стрессе некритичное не дёргает пользователя."""
    calendar(_event("обычная встреча", 30))
    out = engine._get_smart_reminder_suggestions({"last_emotion": "overwhelmed", "mode": "normal"})

    assert out == []


def test_critical_event_breaks_through_stress(calendar, engine):
    """Критичное событие обязано пробиться даже сквозь стресс."""
    calendar(_event("срочно сдать отчёт", 30, description="срочно"))
    out = engine._get_smart_reminder_suggestions({"last_emotion": "overwhelmed", "mode": "normal"})

    assert any("срочно сдать отчёт" in s for s in out), out


def test_movie_mode_does_not_interrupt(calendar, engine):
    calendar(_event("обычная встреча", 30))
    out = engine._get_smart_reminder_suggestions({"last_emotion": "neutral", "mode": "movie"})

    assert out == []


# ─── Health: залипшая сессия не даёт «714 часов» ──────────────────────────────
def test_stale_activity_session_resets(tmp_path, monkeypatch):
    """Незакрытая сессия месячной давности не должна считаться марафоном."""
    import json
    from datetime import datetime, timedelta

    from core import smart_reminders

    path = tmp_path / "activity_tracking.json"
    old_start = (datetime.now() - timedelta(days=30)).isoformat()
    path.write_text(json.dumps({
        "current_session": {"activity_type": "movie", "start_time": old_start},
        "sessions": [], "health_alerts": [],
    }), encoding="utf-8")
    monkeypatch.setattr(smart_reminders.ActivityTracker, "activity_file", path, raising=False)

    tracker = smart_reminders.ActivityTracker()
    tracker.activity_file = path
    tracker.activities = tracker._load_activities()

    assert tracker.get_current_duration() == 0.0        # не 43000 минут
    assert tracker.activities["current_session"] is None  # залипшая сброшена


def test_fresh_movie_session_still_counts(tmp_path):
    """Свежая сессия (полтора часа) считается нормально."""
    import json
    from datetime import datetime, timedelta

    from core import smart_reminders

    path = tmp_path / "activity_tracking.json"
    start = (datetime.now() - timedelta(minutes=90)).isoformat()
    path.write_text(json.dumps({
        "current_session": {"activity_type": "movie", "start_time": start},
        "sessions": [], "health_alerts": [],
    }), encoding="utf-8")

    tracker = smart_reminders.ActivityTracker()
    tracker.activity_file = path
    tracker.activities = tracker._load_activities()

    assert 85 <= tracker.get_current_duration() <= 95   # ~90 минут, не сброшено
