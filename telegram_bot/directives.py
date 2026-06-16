"""
Action directives — let JARVIS (Gemini) actually create reminders, habits and
tasks straight from a conversation, not just role-play that it did.

The model appends hidden blocks at the end of its reply; we execute them against
the durable store and strip them from the visible text. Shared by the Telegram
bot and the Mini App so both behave identically.
"""
import logging
import re
from datetime import datetime

from telegram_bot import reminders as rem
from telegram_bot import agenda

logger = logging.getLogger("jarvis-directives")

_RE_REMINDERS = re.compile(r"\[\[REMINDERS\]\](.*?)\[\[/REMINDERS\]\]", re.S | re.I)
_RE_HABITS = re.compile(r"\[\[HABITS\]\](.*?)\[\[/HABITS\]\]", re.S | re.I)
_RE_TASKS = re.compile(r"\[\[TASKS\]\](.*?)\[\[/TASKS\]\]", re.S | re.I)
_REM_LINE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2})[:.](\d{2})\s*\|\s*(.+)")

_ALL = (_RE_REMINDERS, _RE_HABITS, _RE_TASKS)


def _clean_line(line: str) -> str:
    return line.strip().lstrip("-•*").strip()


async def apply(memory, user_id: int, reply: str, tz) -> tuple[str, list]:
    """Execute any directive blocks in `reply`. Returns (clean_reply, summary)
    where summary is a list of short human strings like '🔁 привычек: 2'."""
    if not reply or not memory:
        return reply, []
    summary = []

    block = _RE_REMINDERS.search(reply)
    if block:
        n = 0
        for line in block.group(1).splitlines():
            lm = _REM_LINE.search(line.strip())
            if not lm:
                continue
            y, mo, d, hh, mm, what = lm.groups()
            try:
                when = datetime(int(y), int(mo), int(d), int(hh), int(mm), tzinfo=tz)
            except ValueError:
                continue
            await memory.add_reminder(user_id, what.strip(), rem.to_utc_iso(when))
            n += 1
        if n:
            summary.append(f"🔔 напоминаний: {n}")

    block = _RE_HABITS.search(reply)
    if block:
        n = 0
        for line in block.group(1).splitlines():
            t = _clean_line(line)
            if t:
                await memory.add_habit(user_id, t)
                n += 1
        if n:
            summary.append(f"🔁 привычек: {n}")

    block = _RE_TASKS.search(reply)
    if block:
        n = 0
        for line in block.group(1).splitlines():
            t = _clean_line(line)
            if t:
                due, title = agenda.parse(t)
                await memory.add_task(user_id, title, due)
                n += 1
        if n:
            summary.append(f"✅ задач: {n}")

    clean = reply
    for rx in _ALL:
        clean = rx.sub("", clean)
    return clean.strip(), summary
