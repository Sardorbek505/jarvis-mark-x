"""Natural-language reminder parser + time helpers for the Telegram bot.

Storage and delivery live in MemoryStore (durable Postgres/SQLite) — see
add_reminder / get_due_reminders there. This module only turns Russian phrases
like «напомни через 30 минут позвонить маме» into a concrete moment in time,
respecting the user's timezone, plus small helpers to move between local time
and the UTC strings we store/compare against.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional


def now_utc_iso() -> str:
    """Current UTC moment as a naive ISO string (same shape we store `due` in)."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def to_utc_iso(when: datetime) -> str:
    """Convert a (possibly tz-aware) local datetime to a naive-UTC ISO string."""
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when.isoformat()


def confirm_label(when: datetime, now: datetime) -> str:
    """Human confirmation like «через 25 мин» / «сегодня в 15:00» (local time)."""
    delta = when - now
    if delta.total_seconds() < 3600:
        mins = max(1, int(delta.total_seconds() / 60))
        return f"через {mins} мин"
    if when.date() == now.date():
        return f"сегодня в {when.strftime('%H:%M')}"
    if when.date() == (now + timedelta(days=1)).date():
        return f"завтра в {when.strftime('%H:%M')}"
    return when.strftime("%d.%m в %H:%M")


def fmt_local(due_utc_iso: str, tz) -> str:
    """Render a stored UTC reminder time back in the user's local zone."""
    try:
        dt = datetime.fromisoformat(due_utc_iso).replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%d.%m в %H:%M")
    except Exception:
        return due_utc_iso


# ── Natural-language parser ────────────────────────────────────────────────────

def parse_reminder(text: str, now: Optional[datetime] = None) -> Optional[tuple]:
    """Parse a reminder phrase into (when, what).

    `now` should be the user's *local* (tz-aware) current time so absolute times
    like «в 15:00» land in the right timezone. `when` is returned in the same
    frame as `now`. Returns None if nothing parseable.
    """
    if now is None:
        now = datetime.now()
    original = text.strip()
    low = original.lower()

    # Cut everything up to and including the trigger word, so leading filler
    # («отлично, напомни мне …») doesn't break the start-anchored regexes below.
    for trg in ("напомни мне", "напомни", "remind me", "remind",
                "поставь напоминание", "таймер на", "таймер"):
        idx = low.find(trg)
        if idx != -1:
            low = low[idx + len(trg):].strip().lstrip(",").strip()
            break

    # «через N минут/часов/секунд» — relative, timezone-independent. Trailing
    # \w* eats word endings (минут→минуты, час→часа) so «what» stays clean.
    m = re.match(r"(?:через\s+)?(\d+)\s*(секунд|сек|минут|мин|часов|часа|час)\w*", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if "сек" in unit:
            delta = timedelta(seconds=n)
        elif "мин" in unit:
            delta = timedelta(minutes=n)
        else:
            delta = timedelta(hours=n)
        what = low[m.end():].strip().lstrip(",").strip() or "таймер"
        return now + delta, what

    # «в HH:MM [что]» — absolute, in the user's local timezone
    m = re.match(r"(?:сегодня\s+)?в\s+(\d{1,2})[:.](\d{2})\s*(.*)", low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        what = m.group(3).strip() or "напоминание"
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when, what

    # «завтра в HH:MM [что]»
    m = re.match(r"завтра\s+в\s+(\d{1,2})[:.](\d{2})\s*(.*)", low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        what = m.group(3).strip() or "напоминание"
        when = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return when, what

    # «через час / полчаса / полтора часа [что]»
    m = re.match(r"через\s+(час|полчаса|полтора\s+часа)\s*(.*)", low)
    if m:
        word = m.group(1)
        what = m.group(2).strip() or "напоминание"
        if "полтора" in word:
            delta = timedelta(hours=1, minutes=30)
        elif "полчаса" in word:
            delta = timedelta(minutes=30)
        else:
            delta = timedelta(hours=1)
        return now + delta, what

    return None
