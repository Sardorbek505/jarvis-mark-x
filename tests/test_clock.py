"""Часы Джарвиса: таймеры, секундомер, будильники, «который час», где я.

Всё на поддельных часах: время двигаем сами и зовём tick() — без потоков
и ожиданий."""
from datetime import datetime, timedelta

import pytest

from core import clock as ck
from core import location as loc


class FakeTime:
    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> float:
        return self.t.timestamp()

    def local(self) -> datetime:
        return self.t

    def go(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def world(tmp_path):
    ft = FakeTime(datetime(2026, 9, 28, 6, 55))          # понедельник
    c = ck.Clock(tmp_path / "clock.json", now=ft.now, local=ft.local)
    said, notes = [], []
    c.say = said.append
    c.notify = lambda title, text: notes.append((title, text))
    return c, ft, said, notes, tmp_path


# ── слова и разбор ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("p,sec", [
    ({"minutes": 10}, 600), ({"hours": 1, "minutes": 30}, 5400), ({"seconds": 45}, 45),
    ({"duration": "полчаса"}, 1800), ({"duration": "1:30"}, 90), ({"duration": "5 минут"}, 300),
    ({"duration": "полторы минуты"}, 90), ({"duration": "час"}, 3600), ({"duration": "2 часа 15 минут"}, 8100),
    ({"duration": "тридцать секунд"}, 30), ({}, None), ({"minutes": 0}, None),
])
def test_parse_duration(p, sec):
    assert ck.parse_duration(p) == sec


def test_words():
    assert ck.say_seconds(3725) == "1 час 2 минуты 5 секунд"
    assert ck.say_seconds(60) == "1 минута" and ck.say_seconds(0) == "0 секунд"
    assert ck.say_seconds(22 * 60) == "22 минуты" and ck.say_seconds(11 * 60) == "11 минут"
    assert ck.clock_face(83) == "1:23" and ck.clock_face(3725) == "1:02:05"
    assert ck.parse_hhmm("7") == (7, 0) and ck.parse_hhmm("6.30") == (6, 30)
    assert ck.parse_hhmm("8 вечера") == (20, 0) and ck.parse_hhmm("2 ночи") == (2, 0)


def test_now_text(world):
    c, *_ = world
    assert c.now_text() == "Сейчас 06:55, понедельник, 28 сентября 2026 года."


# ── таймеры ───────────────────────────────────────────────────────────────────

def test_timer_fires_once_with_label(world):
    c, ft, said, notes, _ = world
    assert c.timer_set(480, "паста") == "Таймер «паста» на 8 минут запущен."
    ft.go(minutes=7)
    c.tick()
    assert not said and "паста" in c.timer_list() and "1 минута" in c.timer_list()
    ft.go(minutes=1, seconds=1)
    c.tick()
    c.tick()
    assert len(said) == 1 and "паста" in said[0] and notes == [("ТАЙМЕР", "Время вышло: паста")]
    assert c.timer_list() == "Таймеров нет."


def test_timer_pause_add_cancel(world):
    c, ft, said, *_ = world
    c.timer_set(300)
    ft.go(minutes=1)
    c.timer_pause()
    ft.go(minutes=10)
    c.tick()
    assert not said and "4 минуты" in c.timer_list() and "паузе" in c.timer_list()
    c.timer_resume()
    assert "Осталось 6 минут" in c.timer_add(120)
    c.timer_set(60, "чай")
    assert c.timer_cancel("чай") == "Таймер отменён." and "чай" not in c.timer_list()
    assert c.timer_cancel("все") == "Таймер отменён." and c.timer_list() == "Таймеров нет."


def test_timers_survive_restart(world):
    c, ft, said, _, tmp = world
    c.timer_set(600, "стирка")
    c.alarm_set("7:30", repeat="weekdays")
    again = ck.Clock(tmp / "clock.json", now=ft.now, local=ft.local)
    assert "стирка" in again.timer_list() and "07:30" in again.alarm_list()


# ── секундомер ────────────────────────────────────────────────────────────────

def test_stopwatch(world):
    c, ft, *_ = world
    assert c.sw_status() == "Секундомер не запущен."
    c.sw_start()
    ft.go(seconds=65)
    assert c.sw_lap() == "Круг 1: 1 минута 5 секунд (всего 1 минута 5 секунд)."
    ft.go(seconds=30)
    assert c.sw_lap().startswith("Круг 2: 30 секунд")
    assert c.sw_stop() == "Стоп. 1 минута 35 секунд."
    ft.go(minutes=5)
    assert "1 минута 35 секунд (стоит)" in c.sw_status()
    assert c.sw_start() == "Секундомер продолжен."
    ft.go(seconds=5)
    assert "1 минута 40 секунд" in c.sw_status()
    c.sw_reset()
    assert c.sw_status() == "Секундомер не запущен."


# ── будильники ────────────────────────────────────────────────────────────────

def test_alarm_rings_then_jarvis_wakes_and_snooze(world):
    c, ft, said, notes, _ = world
    assert c.alarm_set("7:00") == "Будильник на 07:00 — сегодня, через 5 минут."
    ft.go(minutes=5)
    c.tick()
    assert notes == [("БУДИЛЬНИК", "07:00")] and c.nearest() == ("Будильник 07:00", 0.0)
    assert not said                                     # сначала просто звенит
    ft.go(seconds=ck.ALARM_RING_SEC + 1)
    c.tick()
    assert len(said) == 1 and "alarm_snooze" in said[0]  # потом будит голосом
    assert c.alarm_snooze(5) == "Хорошо, разбужу ещё раз через 5 минут."
    ft.go(minutes=5)
    c.tick()
    assert len(notes) == 2                              # зазвонил снова
    assert c.alarm_stop() == "Будильник выключен. Доброе утро, сэр."
    assert c.alarm_list() == "Будильников нет."        # разовый — удалён


def test_alarm_weekdays_skips_weekend(world):
    c, ft, *_ = world
    ft.t = datetime(2026, 10, 2, 8, 0)                  # пятница, после 7:00
    c.alarm_set("7:00", repeat="weekdays")
    nxt = c.next_ring(c.alarms[0])
    assert nxt == datetime(2026, 10, 5, 7, 0)           # понедельник
    ft.t = datetime(2026, 10, 5, 7, 0, 30)
    c.tick()
    assert c._ringing is c.alarms[0]
    c.alarm_stop()
    assert c.alarms and c.next_ring(c.alarms[0]) == datetime(2026, 10, 6, 7, 0)   # повторяющийся остаётся


def test_alarm_tomorrow_and_cancel(world):
    c, ft, *_ = world
    assert "завтра" in c.alarm_set("6:00", label="пробежка")
    c.alarm_set("21:00")
    assert c.alarm_cancel("пробежка") == "Будильник на 06:00 удалён."
    assert c.alarm_cancel("все") == "Будильник на 21:00 удалён."
    assert "не понял" in c.alarm_set("abc")


def test_unattended_alarm_gives_up(world):
    c, ft, said, *_ = world
    c.alarm_set("7:00")
    ft.go(minutes=5)
    c.tick()
    ft.go(seconds=ck.ALARM_GIVE_UP_SEC + 1)
    c.tick()
    assert c._ringing is None and c.alarm_list() == "Будильников нет."


def test_nearest_for_capsule(world):
    c, ft, *_ = world
    assert c.nearest() == ("", 0.0)
    c.alarm_set("7:30")
    assert c.nearest() == ("", 0.0)             # ближайший будильник в капсуле не висит (только звенящий)
    c.sw_start()
    ft.go(seconds=83)
    assert c.nearest() == ("Секундомер · 1:23", 0.0)
    c.timer_set(60, "чай")
    assert c.nearest() == ("чай", ft.now() + 60)


def test_clock_tool_dispatch(world, monkeypatch):
    c, *_ = world
    monkeypatch.setattr(ck, "_clock", c)
    assert ck.clock_tool({"action": "timer_set", "minutes": 3}).startswith("Таймер на 3 минуты")
    assert ck.clock_tool({"action": "now"}).startswith("Сейчас 06:55")
    assert "Будильник на 07:30" in ck.clock_tool({"action": "alarm_set", "time": "07:30"})
    assert "Не понял" in ck.clock_tool({"action": "fly"})


# ── время в городах и где я ───────────────────────────────────────────────────

def test_world_time(monkeypatch):
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(loc, "geocode", lambda city: {"name": "Токио", "timezone": "Asia/Tokyo",
                                                      "lat": 35.7, "lon": 139.7, "country": "Япония"})
    now = datetime(2026, 9, 28, 12, 0, tzinfo=ZoneInfo("Asia/Almaty"))
    text = ck.world_time("Токио", now=now)
    assert text.startswith("В городе Токио сейчас 16:00, понедельник")   # Алматы — UTC+5, Токио — +9


def test_where_prefers_home_city_then_ip(monkeypatch):
    loc._cache.update(at=0.0, place=None)
    monkeypatch.setattr(loc, "_home_city", lambda: "")
    monkeypatch.setattr(loc, "_by_ip", lambda: {"name": "Алматы", "region": "Алматы", "country": "Казахстан",
                                                "lat": 43.2, "lon": 76.9, "source": "ip"})
    assert loc.describe(loc.where()) == "Вы в Алматы, Казахстан (по интернет-подключению, точность — город)."
    loc._cache.update(at=0.0, place=None)
    monkeypatch.setattr(loc, "_home_city", lambda: "Ташкент")
    monkeypatch.setattr(loc, "geocode", lambda c: {"name": "Ташкент", "country": "Узбекистан",
                                                   "lat": 41.3, "lon": 69.2, "timezone": "Asia/Tashkent"})
    assert loc.city() == "Ташкент"
    loc._cache.update(at=0.0, place=None)


def test_distance():
    almaty = {"lat": 43.238, "lon": 76.945}
    tashkent = {"lat": 41.311, "lon": 69.279}
    assert 650 < loc.distance_km(almaty, tashkent) < 700


def test_weather_without_city_uses_location(monkeypatch):
    from actions import weather
    monkeypatch.setattr(loc, "city", lambda: "")
    assert "Для какого города" in weather.weather_action({})


# ── заметки ───────────────────────────────────────────────────────────────────

def test_notes_append_delete_latest(tmp_path, monkeypatch):
    from actions import obsidian as ob
    monkeypatch.setattr(ob, "_config", lambda: {"vault_path": str(tmp_path / "vault"), "inbox_folder": "Inbox",
                                                "daily_folder": "Daily"})
    act = ob.obsidian_action
    assert "Записал" in act({"action": "write", "title": "Список покупок", "content": "хлеб"})
    assert act({"action": "append", "title": "список покупок", "content": "молоко"}) == \
        "Дописал в заметку «Список покупок»."
    assert "молоко" in act({"action": "read", "title": "последняя"})
    assert "корзине" in act({"action": "delete", "title": "Список покупок"})
    assert act({"action": "list"}) == "В базе знаний пока нет заметок."
    assert (tmp_path / "vault" / ".trash" / "Список покупок.md").exists()   # можно вернуть
