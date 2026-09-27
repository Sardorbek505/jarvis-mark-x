"""Свои команды без фразы: по времени, при открытии программы, при запуске.

Command.when — список условий:
  {"on": "time", "at": "09:00", "days": [0, 1, 2, 3, 4]}   по будням в 9:00
  {"on": "app", "app": "obs"}                               когда открывается OBS
  {"on": "start"}                                            при запуске Джарвиса

Scheduler раз в 20 секунд смотрит на часы и список процессов.
- Время срабатывает один раз в день и только в первые WINDOW_MIN минут:
  включили ПК в 11 — команда «в 9:00» уже не запустится задним числом.
  Отметки хранятся в macros.json (fired), так что перезапуск не повторит.
- Программа — когда её процесс появился; что было открыто до запуска
  Джарвиса, не считается.
- Команда с «Спрашивать перед запуском» не выполняется сама: Джарвис
  спрашивает и запускает по «да».
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Callable

logger = logging.getLogger(__name__)

WINDOW_MIN = 10
DAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_DAY_WORDS = {"будни": [0, 1, 2, 3, 4], "будням": [0, 1, 2, 3, 4], "рабочие": [0, 1, 2, 3, 4],
              "выходные": [5, 6], "выходным": [5, 6], "каждый": list(range(7)), "ежедневно": list(range(7))}
_DAY_RX = [r"\bпн\b|понедельн", r"\bвт\b|вторн", r"\bср\b|сред", r"\bчт\b|четверг", r"\bпт\b|пятниц",
           r"\bсб\b|суббот", r"\bвс\b|воскресен"]


def parse_days(days) -> list[int]:
    """[0,2] | «будни» | «пн,ср,пт» | «каждый день» → номера дней (0 — понедельник)."""
    if isinstance(days, (list, tuple, set)):
        out = set()
        for d in days:
            if isinstance(d, int) or str(d).strip().isdigit():
                if 0 <= int(d) <= 6:
                    out.add(int(d))
            else:
                out.update(parse_days(str(d)))
        return sorted(out) or list(range(7))
    text = str(days or "").lower()
    for word, val in _DAY_WORDS.items():
        if word in text:
            return list(val)
    found = sorted({i for i, rx in enumerate(_DAY_RX) if re.search(rx, text)})
    return found or list(range(7))


def parse_at(at) -> str | None:
    """«9», «9:00», «9.30», «21 30» → «09:00» / «21:30»."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:\s*[:.\s]\s*(\d{2}))?\s*", str(at or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def clean_when(when) -> list[dict]:
    out = []
    for w in when or []:
        if not isinstance(w, dict):
            continue
        on = str(w.get("on") or "").strip().lower()
        if on == "time":
            at = parse_at(w.get("at"))
            if at:
                out.append({"on": "time", "at": at, "days": parse_days(w.get("days"))})
        elif on == "app":
            app = str(w.get("app") or "").strip().lower()
            if app:
                out.append({"on": "app", "app": app})
        elif on == "start":
            out.append({"on": "start"})
    return out


def describe_days(days: list[int]) -> str:
    d = sorted(days)
    if d == list(range(7)):
        return "каждый день"
    if d == [0, 1, 2, 3, 4]:
        return "по будням"
    if d == [5, 6]:
        return "по выходным"
    return ", ".join(DAY_NAMES[i] for i in d)


def describe_when(w: dict) -> str:
    if w["on"] == "time":
        return f"{describe_days(w['days'])} в {w['at']}"
    if w["on"] == "app":
        return f"когда открывается {w['app']}"
    return "при запуске Джарвиса"


def _process_names() -> set[str]:
    import psutil
    names = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n:
            names.add(n)
    return names


class Scheduler:
    def __init__(self, store, now: Callable[[], datetime] = datetime.now,
                 processes: Callable[[], set[str]] = _process_names):
        self.store = store
        self.now = now
        self.processes = processes
        self._seen: set[str] | None = None
        self._stop = threading.Event()

    # ── что пора запустить ──
    def due_time(self) -> list[tuple[str, object]]:
        now = self.now()
        today = now.date().isoformat()
        out = []
        for c in self.store.commands:
            if not c.enabled:
                continue
            for w in c.when:
                if w["on"] != "time" or now.weekday() not in w["days"]:
                    continue
                at = datetime.combine(now.date(), datetime.strptime(w["at"], "%H:%M").time())
                key = f"{c.id}@{w['at']}"
                if at <= now < at + timedelta(minutes=WINDOW_MIN) and self.store.fired.get(key) != today:
                    out.append((key, c))
        return out

    def due_apps(self) -> list[tuple[object, str]]:
        watched = [(c, w["app"]) for c in self.store.commands if c.enabled for w in c.when if w["on"] == "app"]
        if not watched:
            self._seen = None
            return []
        try:
            names = self.processes()
        except Exception as exc:
            logger.debug("Свои команды, процессы: %s", exc)
            return []
        before, self._seen = self._seen, names
        if before is None:                     # первый взгляд — только запомнить
            return []
        new = names - before
        return [(c, app) for c, app in watched if any(app in n for n in new)]

    # ── запуск ──
    def fire(self, cmd, why: str) -> None:
        if cmd.confirm:
            self.store.say(f"[СИСТЕМА: {why} пора выполнить свою команду «{cmd.name}». Спроси пользователя "
                           f"одной короткой фразой; если согласится — macro action=\"run\" name=\"{cmd.name}\".]")
            return
        logger.info("Своя команда «%s» — %s", cmd.name, why)
        self.store.log(f"SYS: ⏰ «{cmd.name}» — {why}")
        self.store.run(cmd, {})

    def tick(self) -> None:
        today = self.now().date()
        fired = False
        for key, c in self.due_time():
            self.store.fired[key] = today.isoformat()
            fired = True
            self.fire(c, "по расписанию")
        for c, app in self.due_apps():
            self.fire(c, f"открылась {app}")
        if fired:
            cutoff = (today - timedelta(days=7)).isoformat()
            self.store.fired = {k: v for k, v in self.store.fired.items() if v >= cutoff}
            try:
                self.store.save()
            except OSError as exc:
                logger.debug("Свои команды, отметки: %s", exc)

    def on_start(self) -> None:
        for c in self.store.commands:
            if c.enabled and any(w["on"] == "start" for w in c.when):
                self.fire(c, "при запуске")

    def start(self, every: float = 20.0, start_delay: float = 8.0) -> None:
        def loop():
            if not self._stop.wait(start_delay):
                try:
                    self.on_start()
                except Exception as exc:
                    logger.warning("Свои команды при запуске: %s", exc)
            while not self._stop.wait(every):
                try:
                    self.tick()
                except Exception as exc:
                    logger.debug("Свои команды, расписание: %s", exc)
        threading.Thread(target=loop, daemon=True, name="macro-triggers").start()

    def stop(self) -> None:
        self._stop.set()
