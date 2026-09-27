"""Учёба: расписание пар, задачи и дедлайны, напоминания, режим учёбы.

- Расписание — пары по дням недели (Пн=0 … Вс=6), с чётными/нечётными
  неделями: номер недели считается от начала семестра (1-я — нечётная).
- Задачи — домашки, контрольные, экзамены, проекты: срок, предмет, готово.
- Напоминания (tick): за N минут до пары (по умолчанию 10), дедлайн —
  накануне вечером (19:00) и утром в день сдачи; каждое — один раз.
- Голосом (study_tool): «что завтра», «какая следующая пара», «добавь
  домашку по физике на пятницу», «сделал домашку по матану», «дедлайны»,
  «режим учёбы 25 минут».
- Утренний брифинг берёт отсюда пары на сегодня и ближайшие дедлайны.
Окно — ui_study.py. Файл — study.json в папке данных.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
KINDS = ["лекция", "практика", "семинар", "лабораторная"]
TASK_KINDS = ["домашка", "контрольная", "экзамен", "проект", "другое"]
_MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]
_NUMS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
         "десять": 10, "неделю": 7}


@dataclass
class Lesson:
    subject: str
    weekday: int                       # 0 — понедельник
    start: str                         # «08:30»
    end: str = ""
    room: str = ""
    teacher: str = ""
    kind: str = "лекция"
    weeks: str = "all"                 # all | odd | even
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class Task:
    title: str
    subject: str = ""
    due: str = ""                      # «2026-10-02»
    kind: str = "домашка"
    done: bool = False
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


def hhmm(text: str) -> str:
    m = re.search(r"(\d{1,2})[:.](\d{2})", text or "") or re.search(r"(\d{1,2})", text or "")
    if not m:
        return ""
    h, mnt = int(m.group(1)), int(m.group(2)) if m.lastindex and m.lastindex > 1 else 0
    return f"{h:02d}:{mnt:02d}" if 0 <= h < 24 and 0 <= mnt < 60 else ""


def parse_weekday(text: str) -> int | None:
    t = (text or "").lower().replace("ё", "е")
    for i, w in enumerate(WEEKDAYS):
        if w[:4] in t or WEEKDAYS_SHORT[i].lower() == t.strip():
            return i
    return None


def parse_due(text: str, today: date) -> date | None:
    """«завтра», «в пятницу», «до 5 октября», «5.10», «через неделю», «2026-10-05»."""
    t = (text or "").lower().replace("ё", "е").strip()
    if not t:
        return None
    if "послезавтра" in t:
        return today + timedelta(days=2)
    if "завтра" in t:
        return today + timedelta(days=1)
    if "сегодня" in t:
        return today
    m = re.search(r"через\s+(\d+|\w+)\s*(дн|день|дня|недел)", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMS.get(m.group(1), 1)
        return today + timedelta(days=n * (7 if m.group(2).startswith("недел") else 1))
    if re.search(r"через\s+неделю", t):
        return today + timedelta(days=7)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\s+([а-я]+)", t)
    if m:
        month = next((i + 1 for i, s in enumerate(_MONTHS) if m.group(2).startswith(s)), 0)
        if month:
            return _this_or_next_year(today, month, int(m.group(1)))
    m = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", t)
    if m:
        if m.group(3):
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))
        return _this_or_next_year(today, int(m.group(2)), int(m.group(1)))
    wd = parse_weekday(t)
    if wd is not None:
        ahead = (wd - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    return None


def _this_or_next_year(today: date, month: int, day: int) -> date:
    """Недавно прошедшая дата (до 2 месяцев) — это просрочка в этом году,
    а не срок через год: «реферат до 25 сентября», записанный 28-го."""
    d = date(today.year, month, day)
    return d if d >= today - timedelta(days=60) else date(today.year + 1, month, day)


def say_date(d: date, today: date) -> str:
    diff = (d - today).days
    if diff == 0:
        return "сегодня"
    if diff == 1:
        return "завтра"
    if diff == 2:
        return "послезавтра"
    if diff < 0:
        n = -diff
        word = "день" if n % 10 == 1 and n % 100 != 11 else ("дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14
                                                           else "дней")
        return f"просрочено на {n} {word}"
    if diff < 7:
        return WEEKDAYS[d.weekday()] if d.weekday() >= today.weekday() else "в " + WEEKDAYS[d.weekday()]
    return f"{d.day} {['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'][d.month - 1]}"


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (t or "").lower().replace("ё", "е"))).strip()


_SLANG = {"физра": "культур", "физкультур": "культур", "инфа": "информат", "информатик": "информат",
          "русич": "русск", "литра": "литератур", "матеш": "математ", "алгеб": "алгебр", "геом": "геометр",
          "химоз": "хими", "биол": "биолог", "прога": "программ", "проги": "программ"}


def _abbrev(t: str, words: list[str]) -> bool:
    """t склеен из начал слов подряд (первое — хотя бы 2 буквы)."""
    if not t:
        return True
    if not words:
        return False
    w = words[0]
    for k in range(min(len(w), len(t)), 0, -1):
        if t[:k] == w[:k] and (k >= 2 or len(words) > 1) and _abbrev(t[k:], words[1:]):
            return True
    return False


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


class Study:
    def __init__(self, path: Path, now: Callable[[], datetime] = datetime.now):
        self.path = Path(path)
        self.now = now
        self.lessons: list[Lesson] = []
        self.tasks: list[Task] = []
        self.semester_start = ""               # «2026-09-01» — для чётности недели
        self.remind_min = 10
        self.sent: dict[str, str] = {}         # что уже напомнили: ключ → дата
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.say: Callable[[str], None] = lambda text: None
        self.notify: Callable[[str, str], None] = lambda title, text: None
        self.load()

    # ── файл ──
    def load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:
            logger.warning("Учёба не прочиталась: %s", exc)
            return
        self.lessons = [Lesson(**{k: v for k, v in x.items() if k in Lesson.__dataclass_fields__})
                        for x in d.get("lessons", [])]
        self.tasks = [Task(**{k: v for k, v in x.items() if k in Task.__dataclass_fields__})
                      for x in d.get("tasks", [])]
        self.semester_start = d.get("semester_start", "")
        self.remind_min = int(d.get("remind_min", 10))
        self.sent = dict(d.get("sent", {}))

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "semester_start": self.semester_start,
                                       "remind_min": self.remind_min, "sent": self.sent,
                                       "lessons": [asdict(x) for x in self.lessons],
                                       "tasks": [asdict(x) for x in self.tasks]},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    # ── неделя ──
    def week_parity(self, d: date) -> str:
        """«odd» / «even»: от начала семестра (1-я неделя — нечётная)."""
        try:
            start = date.fromisoformat(self.semester_start)
        except ValueError:
            start = date(d.year if d.month >= 9 else d.year - 1, 9, 1)
        start_monday = start - timedelta(days=start.weekday())     # неделя — с понедельника
        n = ((d - start_monday).days // 7) + 1
        return "odd" if n % 2 else "even"

    def lessons_on(self, d: date) -> list[Lesson]:
        parity = self.week_parity(d)
        return sorted((x for x in self.lessons if x.weekday == d.weekday() and x.weeks in ("all", parity)),
                      key=lambda x: x.start)

    def subjects(self) -> list[str]:
        seen = []
        for s in [x.subject for x in self.lessons] + [t.subject for t in self.tasks]:
            if s and s not in seen:
                seen.append(s)
        return seen

    def match_subject(self, text: str) -> str:
        """«матан» → «Математический анализ», если такое похоже; иначе как сказали."""
        t = _norm(text)
        if not t:
            return ""
        subjects = self.subjects()
        for s in subjects:
            n = _norm(s)
            if t == n or t in n or n in t:
                return s
        # Студенческие словечки, которые не выводятся из названия
        for slang, key in _SLANG.items():
            if t.startswith(slang):
                hit = [s for s in subjects if key in _norm(s)]
                if len(hit) == 1:
                    return hit[0]
        # «матан» = мат(ематический) ан(ализ), «линал» = лин(ейная) ал(гебра)
        for s in subjects:
            if _abbrev(t.replace(" ", ""), _norm(s).split()):
                return s
        # «физра», «англ», «дискра»: одно начало — если оно у одного предмета
        same = [s for s in subjects if any(w.startswith(t[:3]) for w in _norm(s).split())]
        if len(same) == 1:
            return same[0]
        try:
            from rapidfuzz import fuzz
            best = max(self.subjects(), key=lambda s: fuzz.partial_ratio(t, _norm(s)), default="")
            if best and fuzz.partial_ratio(t, _norm(best)) >= 80:
                return best
        except ImportError:
            pass
        return text.strip()

    # ── расписание словами ──
    @staticmethod
    def lesson_text(x: Lesson) -> str:
        parts = [f"{x.start}" + (f"–{x.end}" if x.end else ""), x.subject]
        if x.kind and x.kind != "лекция":
            parts.append(f"({x.kind})")
        if x.room:
            parts.append(f"ауд. {x.room}")
        return " ".join(parts)

    def day_text(self, d: date) -> str:
        today = self.now().date()
        items = self.lessons_on(d)
        head = _cap(say_date(d, today) if abs((d - today).days) < 3 else WEEKDAYS[d.weekday()])
        if not items:
            return f"{head} пар нет."
        return f"{head} {len(items)} пар: " + "; ".join(self.lesson_text(x) for x in items) + "."

    def now_next(self) -> str:
        now = self.now()
        cur = nxt = None
        for x in self.lessons_on(now.date()):
            st = datetime.combine(now.date(), dtime.fromisoformat(x.start))
            en = datetime.combine(now.date(), dtime.fromisoformat(x.end)) if x.end else st + timedelta(minutes=90)
            if st <= now < en:
                cur = (x, en)
            elif st > now and nxt is None:
                nxt = (x, st)
        parts = []
        if cur:
            parts.append(f"Сейчас {cur[0].subject} до {cur[1]:%H:%M}" + (f", ауд. {cur[0].room}" if cur[0].room else ""))
        if nxt:
            mins = int((nxt[1] - now).total_seconds() // 60)
            parts.append(f"следующая — {nxt[0].subject} в {nxt[0].start} (через {mins} мин)"
                         + (f", ауд. {nxt[0].room}" if nxt[0].room else ""))
        if not parts:
            tomorrow = now.date() + timedelta(days=1)
            first = self.lessons_on(tomorrow)
            return "Сегодня пар больше нет." + (f" Завтра первая — {self.lesson_text(first[0])}." if first else "")
        return _cap(", ".join(parts)) + "."

    # ── задачи ──
    def open_tasks(self) -> list[Task]:
        return sorted((t for t in self.tasks if not t.done), key=lambda t: (t.due or "9999", t.title))

    def add_task(self, title: str, subject: str = "", due: str = "", kind: str = "домашка", note: str = "") -> Task:
        today = self.now().date()
        d = parse_due(due, today) if due else None
        t = Task(title=title.strip() or kind, subject=self.match_subject(subject), due=d.isoformat() if d else "",
                 kind=kind if kind in TASK_KINDS else "другое", note=note)
        with self._lock:
            self.tasks.append(t)
            self.save()
        return t

    def find_task(self, text: str) -> list[Task]:
        t = _norm(text)
        pool = self.open_tasks()
        hits = [x for x in pool if t and (t in _norm(x.title + " " + x.subject + " " + x.kind))]
        if not hits and t:
            words = [w[:5] for w in t.split() if len(w) > 2]
            hits = [x for x in pool if any(w in _norm(x.title + " " + x.subject) for w in words)]
        return hits

    def done(self, text: str) -> str:
        hits = self.find_task(text)
        if not hits:
            return "Не нашёл такую задачу среди открытых."
        if len(hits) > 1 and not any(_norm(h.title) == _norm(text) for h in hits):
            return "Какую именно: " + "; ".join(self.task_text(h) for h in hits[:4]) + "?"
        t = hits[0]
        t.done = True
        self.save()
        left = len(self.open_tasks())
        return f"Отметил: {self.task_text(t)}. Осталось задач: {left}."

    def task_text(self, t: Task) -> str:
        today = self.now().date()
        due = f" — {say_date(date.fromisoformat(t.due), today)}" if t.due else ""
        subj = f"{t.subject}: " if t.subject and t.subject.lower() not in t.title.lower() else ""
        kind = "" if t.kind == "домашка" or t.kind in t.title.lower() else f"{t.kind} "
        return f"{subj}{kind}{t.title}{due}"

    def deadlines_text(self, days: int = 7) -> str:
        today = self.now().date()
        soon = [t for t in self.open_tasks() if t.due and date.fromisoformat(t.due) <= today + timedelta(days=days)]
        undated = [t for t in self.open_tasks() if not t.due]
        if not soon and not undated:
            return "Открытых задач нет — всё сделано."
        parts = []
        late = [t for t in soon if date.fromisoformat(t.due) < today]
        if late:
            parts.append("Просрочено: " + "; ".join(self.task_text(t) for t in late))
        near = [t for t in soon if date.fromisoformat(t.due) >= today]
        if near:
            parts.append("Скоро: " + "; ".join(self.task_text(t) for t in near))
        if undated:
            parts.append("Без срока: " + "; ".join(self.task_text(t) for t in undated[:5]))
        return ". ".join(parts) + "."

    # ── напоминания ──
    def tick(self) -> None:
        now = self.now()
        today = now.date()
        for x in self.lessons_on(today):
            st = datetime.combine(today, dtime.fromisoformat(x.start))
            before = (st - now).total_seconds() / 60
            key = f"lesson:{x.id}:{today}"
            if 0 < before <= self.remind_min and key not in self.sent:
                self.sent[key] = today.isoformat()
                text = f"Через {int(round(before))} мин — {x.subject}" + (f", ауд. {x.room}" if x.room else "")
                self.notify("ПАРА", text)
                self.say(f"[СИСТЕМА: напомни пользователю одной короткой фразой: {text}.]")
        for t in self.open_tasks():
            if not t.due:
                continue
            due = date.fromisoformat(t.due)
            eve = (due - timedelta(days=1) == today and now.hour >= 19, f"eve:{t.id}")
            morn = (due == today and now.hour >= 8, f"day:{t.id}")
            for fire, key in (eve, morn):
                if fire and key not in self.sent:
                    self.sent[key] = today.isoformat()
                    when = "завтра" if key.startswith("eve") else "сегодня"
                    text = f"{when} срок: {self.task_text(t).split(' — ')[0]}"
                    self.notify("ДЕДЛАЙН", text[:1].upper() + text[1:])
                    self.say(f"[СИСТЕМА: мягко напомни пользователю одной фразой: {text}.]")
        # старые отметки — прочь (неделя); на диск — только если что-то поменялось
        cutoff = (today - timedelta(days=7)).isoformat()
        fresh = {k: v for k, v in self.sent.items() if v >= cutoff}
        if fresh != getattr(self, "_saved_sent", None):
            self.sent = fresh
            self.save()
            self._saved_sent = dict(fresh)

    def start(self, every: float = 30.0):
        def loop():
            while not self._stop.wait(every):
                try:
                    self.tick()
                except Exception as exc:
                    logger.debug("Учёба, напоминания: %s", exc)
        threading.Thread(target=loop, daemon=True, name="study").start()

    # ── для брифинга ──
    def briefing(self) -> str:
        today = self.now().date()
        parts = []
        items = self.lessons_on(today)
        if items:
            parts.append(f"пары сегодня ({len(items)}): " + "; ".join(self.lesson_text(x) for x in items))
        dl = [t for t in self.open_tasks() if t.due and date.fromisoformat(t.due) <= today + timedelta(days=3)]
        if dl:
            parts.append("дедлайны: " + "; ".join(self.task_text(t) for t in dl))
        return ". ".join(parts)


_study: Study | None = None


def study() -> Study:
    global _study
    if _study is None:
        env = os.getenv("JARVIS_STUDY", "").strip()
        if env:
            path = Path(env)
        else:
            from core.paths import get_data_root
            path = Path(get_data_root()) / "study.json"
        _study = Study(path)
    return _study


def focus(minutes: float = 25, subject: str = "") -> str:
    """Режим учёбы: таймер помидоро через часы Джарвиса."""
    from core.clock import clock
    minutes = max(5, min(120, float(minutes or 25)))
    label = f"учёба{': ' + subject if subject else ''}"
    clock().timer_set(int(minutes * 60), label)
    return (f"Режим учёбы: {int(minutes)} минут{' по ' + subject if subject else ''}. Скажу, когда перерыв. "
            "Телефон — в сторону.")


def study_tool(p: dict) -> str:
    p = p or {}
    a = str(p.get("action") or "today").lower()
    s = study()
    today = s.now().date()
    if a in ("today", "tomorrow", "day"):
        d = today if a == "today" else today + timedelta(days=1)
        if a == "day":
            d = parse_due(str(p.get("when") or ""), today) or today
        return s.day_text(d) if s.lessons else "Расписания ещё нет — добавьте пары в окне «Учёба»."
    if a == "week":
        if not s.lessons:
            return "Расписания ещё нет — добавьте пары в окне «Учёба»."
        monday = today - timedelta(days=today.weekday())
        return " ".join(s.day_text(monday + timedelta(days=i)) for i in range(6)
                        if s.lessons_on(monday + timedelta(days=i)))
    if a == "now":
        return s.now_next() if s.lessons else "Расписания ещё нет — добавьте пары в окне «Учёба»."
    if a == "add_task":
        t = s.add_task(str(p.get("title") or ""), str(p.get("subject") or ""), str(p.get("due") or ""),
                       str(p.get("kind") or "домашка"), str(p.get("note") or ""))
        return f"Записал: {s.task_text(t)}." + ("" if t.due else " Срок не понял — можно сказать «до пятницы».")
    if a == "done":
        return s.done(str(p.get("title") or p.get("subject") or ""))
    if a == "deadlines":
        return s.deadlines_text(int(p.get("days") or 7))
    if a == "delete_task":
        hits = s.find_task(str(p.get("title") or ""))
        if len(hits) != 1:
            return "Не понял, какую задачу удалить." if not hits else "Уточните: " + "; ".join(s.task_text(h) for h in hits)
        s.tasks = [t for t in s.tasks if t.id != hits[0].id]
        s.save()
        return f"Удалил: {s.task_text(hits[0])}."
    if a == "add_lesson":
        wd = parse_weekday(str(p.get("weekday") or ""))
        start = hhmm(str(p.get("start") or ""))
        if wd is None or not start:
            return "Нужны день недели и время начала пары."
        x = Lesson(subject=s.match_subject(str(p.get("subject") or "Пара")), weekday=wd, start=start,
                   end=hhmm(str(p.get("end") or "")), room=str(p.get("room") or ""),
                   kind=str(p.get("kind") or "лекция"), weeks=str(p.get("weeks") or "all"))
        s.lessons.append(x)
        s.save()
        return f"Добавил пару: {WEEKDAYS[wd]}, {s.lesson_text(x)}."
    if a == "focus":
        return focus(p.get("minutes") or 25, str(p.get("subject") or ""))
    return f"Не понял действие «{a}»."
