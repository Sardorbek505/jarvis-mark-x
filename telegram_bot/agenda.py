"""
Agenda helpers — natural-language date parsing and formatting for tasks/events.

parse(text) -> (due_iso | None, title)
  Understands: «завтра в 9:00 ...», «сегодня в 18:30 ...», «послезавтра ...»,
  «в пятницу ...», «25.06 ...», «25.06 в 14:00 ...», «в 9:00 ...».
  Undated tasks (no date phrase) return due=None — a plain todo item.
"""
import re
from datetime import datetime, timedelta

_WEEKDAYS = {
    "понедельник": 0, "пн": 0, "вторник": 1, "вт": 1, "среда": 2, "среду": 2, "ср": 2,
    "четверг": 3, "чт": 3, "пятница": 4, "пятницу": 4, "пт": 4,
    "суббота": 5, "субботу": 5, "сб": 5, "воскресенье": 6, "вс": 6,
}


def _time_in(s: str):
    """Extract 'в HH:MM' from start of string → (hour, minute, rest) or None."""
    m = re.match(r"в\s+(\d{1,2})[:.](\d{2})\s*(.*)", s)
    if m:
        return int(m.group(1)), int(m.group(2)), m.group(3).strip()
    return None


def parse(text: str):
    now = datetime.now()
    original = text.strip()
    low = original.lower()

    def at(day: datetime, rest: str):
        t = _time_in(rest)
        if t:
            h, mi, title = t
            return day.replace(hour=h, minute=mi, second=0, microsecond=0), title
        return day.replace(hour=0, minute=0, second=0, microsecond=0), rest.strip()

    # завтра / сегодня / послезавтра
    for word, days in (("послезавтра", 2), ("завтра", 1), ("сегодня", 0)):
        if low.startswith(word):
            rest = original[len(word):].strip()
            due, title = at(now + timedelta(days=days), rest)
            return due.isoformat(), (title or original)

    # день недели: «в пятницу [в HH:MM] ...»
    m = re.match(r"в\s+(\w+)\s*(.*)", low)
    if m and m.group(1) in _WEEKDAYS:
        target = _WEEKDAYS[m.group(1)]
        ahead = (target - now.weekday()) % 7
        ahead = ahead or 7  # next occurrence (not today)
        rest = original[m.start(2):].strip() if m.group(2) else ""
        due, title = at(now + timedelta(days=ahead), rest)
        return due.isoformat(), (title or original)

    # DD.MM[.YYYY] [в HH:MM] ...
    m = re.match(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\s*(.*)", low)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else now.year
        if y < 100:
            y += 2000
        rest = original[m.start(4):].strip() if m.group(4) else ""
        try:
            base = now.replace(year=y, month=mo, day=d)
            if not m.group(3) and base.date() < now.date():
                base = base.replace(year=y + 1)
            due, title = at(base, rest)
            return due.isoformat(), (title or original)
        except ValueError:
            pass

    # «в HH:MM ...» — сегодня, или завтра если время уже прошло
    t = _time_in(low)
    if t:
        h, mi, title = t
        when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when.isoformat(), (title or original)

    # без даты — обычная задача
    return None, original


def fmt_due(due_iso: str) -> str:
    """Human label for a due datetime."""
    now = datetime.now()
    d = datetime.fromisoformat(due_iso)
    has_time = d.hour or d.minute
    if d.date() == now.date():
        day = "сегодня"
    elif d.date() == (now + timedelta(days=1)).date():
        day = "завтра"
    else:
        day = d.strftime("%d.%m")
    return f"{day} в {d.strftime('%H:%M')}" if has_time else day


def is_today(due_iso: str) -> bool:
    try:
        return datetime.fromisoformat(due_iso).date() == datetime.now().date()
    except Exception:
        return False


def is_overdue(due_iso: str) -> bool:
    try:
        return datetime.fromisoformat(due_iso) < datetime.now()
    except Exception:
        return False


def render_list(tasks: list, title: str = "📋 *Твои задачи*") -> str:
    if not tasks:
        return "Задач нет. Чистое небо ✨\nДобавь: `/task завтра в 9:00 созвон`"
    lines = [title + "\n"]
    for i, t in enumerate(tasks, 1):
        due = t.get("due")
        if due:
            mark = "⚠️" if is_overdue(due) else "🗓"
            lines.append(f"{i}. {mark} *{t['title']}* — {fmt_due(due)}")
        else:
            lines.append(f"{i}. ☐ {t['title']}")
    lines.append("\nЗакрыть: `/done <номер>`")
    return "\n".join(lines)
