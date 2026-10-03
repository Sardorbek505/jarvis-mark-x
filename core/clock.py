"""Часы Джарвиса: таймеры, секундомер, будильники, «который час».

Раньше был только таймер сна; «поставь таймер на 10 минут», «разбуди в 7»,
«засеки время» Джарвис делать не умел, а время брал из промпта, собранного
при подключении, — в долгой сессии оно устаревало.

Всё состояние — в clock.json (время окончания таймеров — по часам стены,
поэтому перезапуск их не теряет). Проверка раз в полсекунды в своём потоке;
в тестах её зовут напрямую (tick) на поддельных часах.

Когда что-то сработало: звук (Windows, файлы из C:\\Windows\\Media), голос
Джарвиса (say — указание в Live-сессию) и событие в журнал и капсулу (notify).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря"]
REPEATS = {"once": "один раз", "daily": "каждый день", "weekdays": "по будням", "weekends": "по выходным"}
ALARM_RING_SEC = 20.0          # сколько звенит перед тем, как Джарвис заговорит
ALARM_GIVE_UP_SEC = 5 * 60     # никто не выключил — перестать звенеть
ALARM_REPEAT_SEC = 60.0        # пока не выключили — звенеть снова раз в минуту
TIMER_RING_SEC = 6.0


# ── слова ─────────────────────────────────────────────────────────────────────

def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def say_seconds(sec: float) -> str:
    """3725 → «1 час 2 минуты 5 секунд». Для голоса, без нулевых частей."""
    sec = int(round(max(0.0, sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} {plural(h, 'час', 'часа', 'часов')}")
    if m:
        parts.append(f"{m} {plural(m, 'минута', 'минуты', 'минут')}")
    if s or not parts:
        parts.append(f"{s} {plural(s, 'секунда', 'секунды', 'секунд')}")
    return " ".join(parts)


def clock_face(sec: float) -> str:
    """Для капсулы и журнала: 83 → «1:23», 3725 → «1:02:05»."""
    sec = int(max(0.0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_WORD_NUM = {"одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5,
             "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "пятнадцать": 15,
             "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "полтора": 1.5,
             "полторы": 1.5}


def parse_duration(p: dict) -> float | None:
    """Секунды из параметров: hours/minutes/seconds числами или duration
    текстом («полчаса», «1:30», «90 секунд», «полторы минуты», «час»)."""
    total, seen = 0.0, False
    for key, k in (("hours", 3600), ("minutes", 60), ("seconds", 1)):
        v = p.get(key)
        if v not in (None, ""):
            try:
                total += float(v) * k
                seen = True
            except (TypeError, ValueError):
                pass
    if seen:
        return total if total > 0 else None
    text = str(p.get("duration") or "").lower().replace(",", ".")
    if not text:
        return None
    if "полчаса" in text:
        return 1800.0
    m = re.fullmatch(r"\s*(\d+):(\d{2})(?::(\d{2}))?\s*", text)
    if m:                                        # «1:30» — минуты:секунды, «1:30:00» — часы
        a, b, c = int(m[1]), int(m[2]), m[3]
        return float(a * 3600 + b * 60 + int(c)) if c else float(a * 60 + b)
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?|[а-я]+)?\s*(час|мин|сек)", text):
        n = float(num) if num and num[0].isdigit() else _WORD_NUM.get(num, 1.0)
        total += n * {"час": 3600, "мин": 60, "сек": 1}[unit]
    return total or None


def parse_hhmm(text: str) -> tuple[int, int]:
    """«7», «07:30», «7.30», «19 30», «7 утра», «8 вечера» → (часы, минуты)."""
    t = str(text or "").lower()
    m = re.search(r"(\d{1,2})(?:[:.\s](\d{2}))?", t)
    if not m:
        raise ValueError(f"не понял время «{text}»")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if re.search(r"веч|ночи|pm", t) and h < 12 and not ("ночи" in t and h <= 4):
        h += 12
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"не понял время «{text}»")
    return h, mi


# ── данные ────────────────────────────────────────────────────────────────────

@dataclass
class Timer:
    id: int
    label: str
    total: float
    end: float = 0.0               # когда сработает (time.time); 0 — на паузе
    left: float = 0.0              # сколько осталось на паузе


@dataclass
class Alarm:
    id: int
    time: str                      # «07:30»
    label: str = ""
    repeat: str = "once"           # once | daily | weekdays | weekends
    date: str = ""                 # для «один раз»: на какой день
    last: str = ""                 # день, когда уже звенел
    snooze_until: float = 0.0


@dataclass
class Stopwatch:
    started: float = 0.0           # идёт с этого момента (time.time); 0 — стоит
    elapsed: float = 0.0           # набежало до последней остановки
    laps: list = field(default_factory=list)

    def value(self, now: float) -> float:
        return self.elapsed + (now - self.started if self.started else 0.0)


# ── часы ──────────────────────────────────────────────────────────────────────

class Clock:
    def __init__(self, path: str | Path | None = None, now: Callable[[], float] = time.time,
                 local: Callable[[], datetime] | None = None):
        self.path = Path(path) if path else None
        self.now = now
        self.local = local or (lambda: datetime.fromtimestamp(self.now()))
        self.say: Callable[[str], None] = lambda text: None
        self.notify: Callable[[str, str], None] = lambda title, text: None
        self.sound = Sound()
        self._lock = threading.RLock()
        self.timers: list[Timer] = []
        self.alarms: list[Alarm] = []
        self.stopwatch = Stopwatch()
        self._next_id = 1
        self._ringing: Alarm | None = None
        self._ring_started = 0.0
        self._ring_spoken = 0.0
        self._thread: threading.Thread | None = None
        self._load()

    # ── хранение ──────────────────────────────────────────────────────────────
    def _load(self):
        if not self.path or not self.path.is_file():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.timers = [Timer(**t) for t in d.get("timers", [])]
            self.alarms = [Alarm(**a) for a in d.get("alarms", [])]
            self.stopwatch = Stopwatch(**d.get("stopwatch", {}))
            self._next_id = int(d.get("next_id", 1))
        except Exception as exc:
            logger.warning("clock.json не читается: %s", exc)

    def _save(self):
        if not self.path:
            return
        data = {"timers": [asdict(t) for t in self.timers], "alarms": [asdict(a) for a in self.alarms],
                "stopwatch": asdict(self.stopwatch), "next_id": self._next_id}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as exc:
            logger.warning("clock.json не сохранён: %s", exc)

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    # ── время ─────────────────────────────────────────────────────────────────
    def now_text(self) -> str:
        d = self.local()
        return (f"Сейчас {d:%H:%M}, {WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]} "
                f"{d.year} года.")

    # ── таймеры ───────────────────────────────────────────────────────────────
    def _find_timers(self, which: str) -> list[Timer]:
        w = (which or "").strip().lower()
        if w in ("все", "всё", "all"):
            return list(self.timers)
        if w:
            hit = [t for t in self.timers if w in t.label.lower() or w == str(t.id)]
            if hit:
                return hit
        return self.timers[-1:]                 # без уточнения — последний поставленный

    def timer_set(self, seconds: float | None, label: str = "") -> str:
        if not seconds or seconds <= 0:
            return "На сколько поставить таймер, сэр?"
        with self._lock:
            t = Timer(self._id(), (label or "").strip(), float(seconds), self.now() + float(seconds))
            self.timers.append(t)
            self._save()
        return f"Таймер{(' «' + t.label + '»') if t.label else ''} на {say_seconds(seconds)} запущен."

    def timer_left(self, t: Timer) -> float:
        return t.left if not t.end else max(0.0, t.end - self.now())

    def timer_list(self) -> str:
        with self._lock:
            if not self.timers:
                return "Таймеров нет."
            parts = []
            for t in sorted(self.timers, key=self.timer_left):
                name = f"«{t.label}»" if t.label else f"на {say_seconds(t.total)}"
                state = " (на паузе)" if not t.end else ""
                parts.append(f"{name} — осталось {say_seconds(self.timer_left(t))}{state}")
        return "Таймеры: " + "; ".join(parts) + "."

    def timer_cancel(self, which: str = "") -> str:
        with self._lock:
            gone = self._find_timers(which)
            if not gone:
                return "Таймеров нет."
            self.timers = [t for t in self.timers if t not in gone]
            self._save()
        return "Все таймеры отменены." if len(gone) > 1 else "Таймер отменён."

    def timer_add(self, seconds: float | None, which: str = "") -> str:
        with self._lock:
            ts = self._find_timers(which)
            if not ts or not seconds:
                return "Какой таймер продлить и на сколько, сэр?"
            t = ts[0]
            if t.end:
                t.end += seconds
            else:
                t.left += seconds
            t.total += seconds
            self._save()
            return f"Добавил {say_seconds(seconds)}. Осталось {say_seconds(self.timer_left(t))}."

    def timer_pause(self, which: str = "") -> str:
        with self._lock:
            ts = [t for t in self._find_timers(which) if t.end]
            for t in ts:
                t.left, t.end = max(0.0, t.end - self.now()), 0.0
            self._save()
        return "Таймер на паузе." if ts else "Нечего ставить на паузу."

    def timer_resume(self, which: str = "") -> str:
        with self._lock:
            ts = [t for t in self._find_timers(which) if not t.end]
            for t in ts:
                t.end, t.left = self.now() + t.left, 0.0
            self._save()
        return "Таймер продолжен." if ts else "Нечего продолжать."

    # ── секундомер ────────────────────────────────────────────────────────────
    def sw_start(self) -> str:
        with self._lock:
            sw = self.stopwatch
            if sw.started:
                return f"Секундомер уже идёт: {say_seconds(sw.value(self.now()))}."
            sw.started = self.now()
            self._save()
            return "Секундомер пошёл." if not sw.elapsed else "Секундомер продолжен."

    def sw_stop(self) -> str:
        with self._lock:
            sw = self.stopwatch
            if not sw.started:
                return "Секундомер не запущен." if not sw.elapsed else \
                    f"Секундомер уже стоит: {say_seconds(sw.elapsed)}."
            sw.elapsed, sw.started = sw.value(self.now()), 0.0
            self._save()
            return f"Стоп. {say_seconds(sw.elapsed)}."

    def sw_lap(self) -> str:
        with self._lock:
            sw = self.stopwatch
            if not sw.started:
                return "Секундомер не запущен."
            v = sw.value(self.now())
            sw.laps.append(v)
            self._save()
            prev = sw.laps[-2] if len(sw.laps) > 1 else 0.0
            return f"Круг {len(sw.laps)}: {say_seconds(v - prev)} (всего {say_seconds(v)})."

    def sw_reset(self) -> str:
        with self._lock:
            self.stopwatch = Stopwatch()
            self._save()
        return "Секундомер сброшен."

    def sw_status(self) -> str:
        sw = self.stopwatch
        if not sw.started and not sw.elapsed:
            return "Секундомер не запущен."
        v = say_seconds(sw.value(self.now()))
        return f"На секундомере {v}" + ("" if sw.started else " (стоит)") + \
            (f", кругов: {len(sw.laps)}." if sw.laps else ".")

    # ── будильники ────────────────────────────────────────────────────────────
    def next_ring(self, a: Alarm, after: datetime | None = None) -> datetime | None:
        """Ближайший звонок будильника (местное время) не раньше after."""
        after = after or self.local()
        h, m = parse_hhmm(a.time)
        if a.snooze_until:
            return datetime.fromtimestamp(a.snooze_until)
        if a.repeat == "once":
            day = datetime.strptime(a.date, "%Y-%m-%d") if a.date else after
            at = day.replace(hour=h, minute=m, second=0, microsecond=0)
            return at if a.last != at.date().isoformat() else None
        for i in range(8):
            d = (after + timedelta(days=i)).replace(hour=h, minute=m, second=0, microsecond=0)
            if d.date().isoformat() == a.last or (i == 0 and d < after - timedelta(minutes=2)):
                continue
            wd = d.weekday()
            if a.repeat == "daily" or (a.repeat == "weekdays" and wd < 5) or (a.repeat == "weekends" and wd >= 5):
                return d
        return None

    def alarm_set(self, when: str, label: str = "", repeat: str = "once") -> str:
        try:
            h, m = parse_hhmm(when)
        except ValueError as exc:
            return f"Сэр, {exc}."
        repeat = repeat if repeat in REPEATS else "once"
        now = self.local()
        with self._lock:
            a = Alarm(self._id(), f"{h:02d}:{m:02d}", (label or "").strip(), repeat)
            if repeat == "once":
                day = now.date() if (h, m) > (now.hour, now.minute) else now.date() + timedelta(days=1)
                a.date = day.isoformat()
            self.alarms.append(a)
            self._save()
        at = self.next_ring(a, now)
        when_txt = "сегодня" if at and at.date() == now.date() else \
            "завтра" if at and at.date() == now.date() + timedelta(days=1) else \
            (WEEKDAYS[at.weekday()] if at else "")
        left = say_seconds((at - now).total_seconds()) if at else ""
        rep = "" if repeat == "once" else f", {REPEATS[repeat]}"
        return (f"Будильник на {a.time}{rep} — {when_txt}, через {left}."
                + (f" «{a.label}»." if a.label else ""))

    def alarm_list(self) -> str:
        with self._lock:
            if not self.alarms:
                return "Будильников нет."
            parts = [f"{a.time} {REPEATS.get(a.repeat, '')}" + (f" «{a.label}»" if a.label else "")
                     for a in sorted(self.alarms, key=lambda a: a.time)]
        return "Будильники: " + "; ".join(p.replace(" один раз", "") for p in parts) + "."

    def alarm_cancel(self, which: str = "") -> str:
        w = (which or "").strip().lower()
        with self._lock:
            if w in ("все", "всё", "all"):
                gone = list(self.alarms)
            elif w:
                try:
                    h, m = parse_hhmm(w)
                    key = f"{h:02d}:{m:02d}"
                except ValueError:
                    key = None
                gone = [a for a in self.alarms if a.time == key or (w and w in a.label.lower())]
            else:
                gone = self.alarms[-1:]
            if not gone:
                return "Такого будильника нет."
            self.alarms = [a for a in self.alarms if a not in gone]
            if self._ringing in gone:
                self._stop_ringing()
            self._save()
        return "Все будильники удалены." if len(gone) > 1 else f"Будильник на {gone[0].time} удалён."

    def alarm_stop(self) -> str:
        with self._lock:
            if not self._ringing:
                return "Будильник не звенит."
            self._stop_ringing()
            self._save()
        return "Будильник выключен. Доброе утро, сэр."

    def alarm_snooze(self, minutes: float = 5) -> str:
        minutes = float(minutes or 5)
        with self._lock:
            a = self._ringing
            if not a:
                return "Будильник не звенит."
            self._stop_ringing(done=False)
            a.snooze_until = self.now() + minutes * 60
            self._save()
        return f"Хорошо, разбужу ещё раз через {say_seconds(minutes * 60)}."

    def _stop_ringing(self, done: bool = True):
        a = self._ringing
        self._ringing = None
        self.sound.stop()
        if a and done:
            a.snooze_until = 0.0
            if a.repeat == "once":
                self.alarms = [x for x in self.alarms if x is not a]

    # ── капсула ───────────────────────────────────────────────────────────────
    def nearest(self) -> tuple[str, float]:
        """(подпись, время окончания для обратного отсчёта или 0)."""
        with self._lock:
            if self._ringing:
                return f"Будильник {self._ringing.time}", 0.0
            running = [t for t in self.timers if t.end]
            if running:
                t = min(running, key=lambda t: t.end)
                return (t.label or "Таймер"), t.end
            if self.stopwatch.started:
                return f"Секундомер · {clock_face(self.stopwatch.value(self.now()))}", 0.0
            # Ближайший будильник в капсуле не показываем: владелец — «этот текст
            # бесполезен». Видно только то, что идёт сейчас: таймер, секундомер,
            # звенящий будильник.
        return "", 0.0

    # ── ход часов ─────────────────────────────────────────────────────────────
    def tick(self):
        now, local = self.now(), self.local()
        fired: list[Timer] = []
        with self._lock:
            for t in list(self.timers):
                if t.end and t.end <= now:
                    self.timers.remove(t)
                    fired.append(t)
            ring: Alarm | None = None
            if not self._ringing:
                for a in self.alarms:
                    at = self.next_ring(a, local)
                    if at and at <= local and (local - at).total_seconds() < 120:
                        ring = a
                        break
            if ring:
                ring.last = local.date().isoformat()
                ring.snooze_until = 0.0
                self._ringing, self._ring_started, self._ring_spoken = ring, now, 0.0
            if fired or ring:
                self._save()
        for t in fired:
            self._timer_fired(t)
        if ring:
            logger.info("Будильник %s «%s» звенит", ring.time, ring.label)
            self.notify("БУДИЛЬНИК", f"{ring.time}" + (f" — {ring.label}" if ring.label else ""))
            self.sound.play("alarm", loop=True)
        self._keep_ringing(now)

    def _timer_fired(self, t: Timer):
        what = f"«{t.label}»" if t.label else f"на {say_seconds(t.total)}"
        logger.info("Таймер %s сработал", what)
        self.notify("ТАЙМЕР", f"Время вышло: {t.label or say_seconds(t.total)}")
        self.sound.play("timer", seconds=TIMER_RING_SEC)
        self.say(f"[СИСТЕМА: сработал таймер {what}. Скажи пользователю одной короткой фразой, "
                 "что время вышло" + (f" ({t.label})" if t.label else "") + ".]")

    def _keep_ringing(self, now: float):
        a = self._ringing
        if not a:
            return
        rang = now - self._ring_started
        if rang > ALARM_GIVE_UP_SEC:
            logger.info("Будильник %s: никто не выключил — перестаю звенеть", a.time)
            with self._lock:
                self._stop_ringing()
                self._save()
            return
        # Позвенел — Джарвис говорит сам (звук на это время стихает), и так
        # раз в минуту, пока будильник не выключат или не отложат.
        if rang >= ALARM_RING_SEC and now - self._ring_spoken >= ALARM_REPEAT_SEC:
            self._ring_spoken = now
            self.sound.stop()
            label = f" («{a.label}»)" if a.label else ""
            self.say(f"[СИСТЕМА: звенит будильник на {a.time}{label}, сейчас {self.local():%H:%M}. "
                     "Разбуди пользователя одной-двумя бодрыми фразами и предложи отложить на 5 минут. "
                     "Скажет «отложи» — вызови clock с action=\"alarm_snooze\", «выключи»/«встаю» — "
                     "action=\"alarm_stop\". Сам будильник не выключай.]")
            threading.Timer(12.0, lambda: self._ringing is a and self.sound.play("alarm", loop=True)).start()

    def start(self):
        if self._thread:
            return

        def run():
            while True:
                time.sleep(0.5)
                try:
                    self.tick()
                except Exception as exc:
                    logger.warning("Часы: %s", exc)
        self._thread = threading.Thread(target=run, daemon=True, name="clock")
        self._thread.start()


class Sound:
    """Звук будильника и таймера. Только Windows (winsound); без него — тишина,
    будит голос Джарвиса."""

    FILES = {"alarm": ("Alarm01.wav", "Alarm02.wav", "Ring01.wav"),
             "timer": ("Alarm02.wav", "Alarm01.wav", "notify.wav")}

    def __init__(self):
        self.playing = False
        self._stop_at: threading.Timer | None = None

    def _file(self, kind: str) -> str | None:
        media = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media"
        return next((str(media / f) for f in self.FILES[kind] if (media / f).is_file()), None)

    def play(self, kind: str, loop: bool = False, seconds: float = 0.0):
        self.playing = True
        try:
            import winsound
        except ImportError:
            return
        path = self._file(kind)
        try:
            if path:
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                                   | (winsound.SND_LOOP if loop or seconds else 0))
            else:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception as exc:
            logger.debug("Звук %s: %s", kind, exc)
        if seconds:
            self._stop_at = threading.Timer(seconds, self.stop)
            self._stop_at.daemon = True
            self._stop_at.start()

    def stop(self):
        self.playing = False
        if self._stop_at:
            self._stop_at.cancel()
            self._stop_at = None
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except Exception:
            pass


_clock: Clock | None = None


def clock() -> Clock:
    global _clock
    if _clock is None:
        try:
            from core.paths import get_data_root
            path = Path(get_data_root()) / "clock.json"
        except Exception:
            path = None
        _clock = Clock(path)
    return _clock


# ── инструмент ────────────────────────────────────────────────────────────────

def clock_tool(p: dict) -> str:
    p = p or {}
    a = (p.get("action") or "now").strip().lower()
    c = clock()
    which = str(p.get("label") or p.get("which") or "")
    if a in ("now", "time", "date"):
        return c.now_text()
    if a == "world_time":
        return world_time(str(p.get("city") or ""))
    if a == "timer_set":
        return c.timer_set(parse_duration(p), str(p.get("label") or ""))
    if a == "timer_list":
        return c.timer_list()
    if a == "timer_cancel":
        return c.timer_cancel(which)
    if a == "timer_add":
        return c.timer_add(parse_duration(p), which)
    if a == "timer_pause":
        return c.timer_pause(which)
    if a == "timer_resume":
        return c.timer_resume(which)
    if a == "stopwatch_start":
        return c.sw_start()
    if a == "stopwatch_stop":
        return c.sw_stop()
    if a == "stopwatch_lap":
        return c.sw_lap()
    if a == "stopwatch_reset":
        return c.sw_reset()
    if a == "stopwatch_status":
        return c.sw_status()
    if a == "alarm_set":
        return c.alarm_set(str(p.get("time") or ""), str(p.get("label") or ""), str(p.get("repeat") or "once"))
    if a == "alarm_list":
        return c.alarm_list()
    if a == "alarm_cancel":
        return c.alarm_cancel(str(p.get("time") or which))
    if a == "alarm_stop":
        return c.alarm_stop()
    if a == "alarm_snooze":
        return c.alarm_snooze(float(p.get("minutes") or 5))
    return f"Не понял действие «{a}»."


def world_time(city: str, now: datetime | None = None) -> str:
    """Время в другом городе: пояс — из геокодера (core/location.py)."""
    if not city.strip():
        return "В каком городе, сэр?"
    from core import location
    place = location.geocode(city)
    if not place or not place.get("timezone"):
        return f"Не нашёл город «{city}»."
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(place["timezone"])
    except Exception:
        return f"Не знаю часовой пояс {place['timezone']} — на Windows нужен пакет tzdata."
    there = (now or datetime.now(tz=tz)).astimezone(tz)
    here = (now or datetime.now().astimezone()).astimezone()
    diff = round((there.utcoffset() - here.utcoffset()).total_seconds() / 3600, 1)
    rel = "" if not diff else (f", на {abs(diff):g} {plural(int(abs(diff)), 'час', 'часа', 'часов')} "
                               + ("впереди" if diff > 0 else "позади"))
    return f"В городе {place['name']} сейчас {there:%H:%M}, {WEEKDAYS[there.weekday()]}{rel}."
