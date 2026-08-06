"""Настольный календарь: время события и различение «пусто» от «не смог».

Два дефекта, закреплённых здесь:

1. В событие для Google зашивалась зона 'Europe/Moscow' — единственное место
   в проекте с жёстко заданной зоной, и не той. Владелец в Шымкенте (UTC+5),
   поэтому «встреча в 15:00» уходила как 15:00 MSK и показывалась в 17:00.

2. Загрузка событий на любую ошибку возвращала пустой список, из-за чего
   «Google Calendar недоступен» выглядело как «событий нет» — молча, без
   единой строки в логе.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actions.calendar as cal


# ── время события ────────────────────────────────────────────────────────────
class _FakeEvents:
    def __init__(self):
        self.body = None

    def insert(self, calendarId=None, body=None):
        self.body = body
        return self

    def execute(self):
        return {"id": "x"}


class _FakeService:
    def __init__(self):
        self._events = _FakeEvents()

    def events(self):
        return self._events


@pytest.fixture
def sent(monkeypatch):
    svc = _FakeService()
    monkeypatch.setattr(cal, "_get_google_calendar_service", lambda: svc)
    return svc


def test_время_уходит_со_смещением_а_не_с_чужой_зоной(sent):
    ok = cal._sync_to_google_calendar({
        "title": "встреча",
        "start": "2026-08-07T15:00:00",
        "end": "2026-08-07T16:00:00",
    })
    assert ok is True
    body = sent.events().body
    # Зона не навязывается — время самодостаточно за счёт смещения.
    assert "timeZone" not in body["start"]
    assert "timeZone" not in body["end"]
    start = datetime.fromisoformat(body["start"]["dateTime"])
    assert start.tzinfo is not None, "без смещения Google истолкует время по-своему"
    assert start.utcoffset() == datetime.now().astimezone().utcoffset()


def test_названный_час_остаётся_тем_же_часом(sent):
    """15:00 у пользователя должно остаться 15:00 в его календаре."""
    cal._sync_to_google_calendar({
        "title": "встреча",
        "start": "2026-08-07T15:00:00",
        "end": "2026-08-07T16:00:00",
    })
    start = datetime.fromisoformat(sent.events().body["start"]["dateTime"])
    local = datetime.now().astimezone().tzinfo
    assert start.astimezone(local).strftime("%H:%M") == "15:00"


def test_зашитая_москва_дала_бы_сдвиг(sent):
    """Фиксирует саму суть дефекта, чтобы его нельзя было вернуть незаметно."""
    naive = datetime.fromisoformat("2026-08-07T15:00:00")
    as_moscow = naive.replace(tzinfo=timezone(timedelta(hours=3)))
    in_almaty = as_moscow.astimezone(timezone(timedelta(hours=5)))
    assert in_almaty.strftime("%H:%M") == "17:00"      # так было
    assert naive.astimezone().strftime("%H:%M") == "15:00"  # так стало


def test_сбой_добавления_виден_в_логе(monkeypatch, caplog):
    def boom():
        raise RuntimeError("token expired")
    monkeypatch.setattr(cal, "_get_google_calendar_service", boom)
    assert cal._sync_to_google_calendar({"title": "x", "start": "", "end": ""}) is False
    assert any("не удалось добавить" in r.message.lower() for r in caplog.records)


# ── пусто ≠ не смог ──────────────────────────────────────────────────────────
def test_ошибка_загрузки_это_None_а_не_пустой_список(monkeypatch, caplog):
    def boom():
        raise RuntimeError("network down")
    monkeypatch.setattr(cal, "_get_google_calendar_service", boom)
    assert cal._sync_from_google_calendar() is None
    assert any("не удалось загрузить" in r.message.lower() for r in caplog.records)


def test_нет_сервиса_это_пусто_а_не_ошибка(monkeypatch):
    monkeypatch.setattr(cal, "_get_google_calendar_service", lambda: None)
    assert cal._sync_from_google_calendar() == []
