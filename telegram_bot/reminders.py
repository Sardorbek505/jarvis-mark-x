"""Simple reminder store + natural-language parser for the Telegram bot."""
import json
import re
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

_BASE = Path(__file__).resolve().parent.parent
_FILE = _BASE / "config" / "bot_reminders.json"
logger = logging.getLogger(__name__)


# ── Storage ────────────────────────────────────────────────────────────────────

def _load() -> List[Dict]:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(reminders: List[Dict]) -> None:
    _FILE.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")


def add_reminder(user_id: int, text: str, when: datetime) -> str:
    reminders = _load()
    reminders.append({
        "id": int(time.time() * 1000),
        "user_id": user_id,
        "text": text,
        "when": when.isoformat(),
        "sent": False,
    })
    _save(reminders)
    # Format time nicely
    now = datetime.now()
    delta = when - now
    if delta.total_seconds() < 3600:
        mins = max(1, int(delta.total_seconds() / 60))
        time_label = f"через {mins} мин"
    elif when.date() == now.date():
        time_label = f"сегодня в {when.strftime('%H:%M')}"
    elif when.date() == (now + timedelta(days=1)).date():
        time_label = f"завтра в {when.strftime('%H:%M')}"
    else:
        time_label = when.strftime("%d.%m в %H:%M")
    return f"✅ Напомню: «{text}» — {time_label}"


def get_due(now: Optional[datetime] = None) -> List[Dict]:
    if now is None:
        now = datetime.now()
    return [r for r in _load() if not r["sent"] and datetime.fromisoformat(r["when"]) <= now]


def mark_sent(reminder_id: int) -> None:
    reminders = _load()
    for r in reminders:
        if r["id"] == reminder_id:
            r["sent"] = True
    _save(reminders)


def list_reminders(user_id: int) -> str:
    reminders = [r for r in _load() if not r["sent"] and r["user_id"] == user_id]
    if not reminders:
        return "У вас нет активных напоминаний."
    lines = ["📋 Ваши напоминания:"]
    for r in reminders:
        when = datetime.fromisoformat(r["when"])
        lines.append(f"  • {when.strftime('%d.%m в %H:%M')} — {r['text']}")
    return "\n".join(lines)


# ── Natural-language parser ────────────────────────────────────────────────────

def parse_reminder(text: str) -> Optional[tuple]:
    """
    Parse 'напомни мне через 30 минут позвонить маме' into (when: datetime, what: str).
    Returns None if can't parse.
    """
    now = datetime.now()
    original = text.strip()
    low = original.lower()

    # Strip leading trigger words
    for prefix in ("напомни мне", "напомни", "remind me", "remind"):
        if low.startswith(prefix):
            low = low[len(prefix):].strip()
            original = original[len(prefix):].strip()
            break

    # ── "через N минут/часов" ─────────────────────────────────────────────────
    m = re.match(r"через\s+(\d+)\s*(минут|мин|часов|часа|час|секунд|сек)", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if "мин" in unit:
            delta = timedelta(minutes=n)
        elif "час" in unit:
            delta = timedelta(hours=n)
        else:
            delta = timedelta(seconds=n)
        what = low[m.end():].strip().lstrip(",").strip()
        what = what or "таймер"
        return now + delta, what

    # ── "в HH:MM [что]" ──────────────────────────────────────────────────────
    m = re.match(r"(?:сегодня\s+)?в\s+(\d{1,2})[:.](\d{2})\s*(.*)", low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        what = m.group(3).strip() or "напоминание"
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when, what

    # ── "завтра в HH:MM [что]" ───────────────────────────────────────────────
    m = re.match(r"завтра\s+в\s+(\d{1,2})[:.](\d{2})\s*(.*)", low)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        what = m.group(3).strip() or "напоминание"
        when = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return when, what

    # ── "через час [что]" ────────────────────────────────────────────────────
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
