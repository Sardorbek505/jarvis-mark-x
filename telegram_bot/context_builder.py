"""Single source of the per-user system-prompt context.

Both the Telegram bot (`bot.py`) and the Render entry (`render_app.py`) feed the
SAME context into Gemini. This module is that one source — they used to keep
two copies that silently drifted.
"""
from telegram_bot import user_context
from telegram_bot import personas

_WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _fmt_class(c: dict) -> str:
    s = (f"{c['time']} " if c.get("time") else "") + c["subject"]
    if c.get("location"):
        s += f" ({c['location']})"
    return s


def build_context(memory, cfg, uid: int) -> str:
    """Compose the live per-user context string injected into the system prompt."""
    parts = [user_context.describe(uid, cfg.default_city, cfg.timezone)]

    mem = memory.cached_block(uid)
    if mem:
        parts.append(mem)

    persona = personas.overlay(memory.cached_mode(uid))
    if persona:
        parts.append(persona)

    contacts = memory.cached_contacts(uid)
    if contacts:
        parts.append("КОМУ можно писать (имя — кто это, для верного тона): " + "; ".join(
            (c["alias"] + (f" — {c['note']}" if c.get("note") else "")) for c in contacts
        ) + ".")
    else:
        parts.append("Контактов для отправки пока нет (пользователь добавляет их через /addcontact).")

    sched = memory.cached_schedule(uid)
    if sched:
        wd = user_context.local_now(uid, cfg.timezone).weekday()
        today = [c for c in sched if c["weekday"] == wd]
        if today:
            parts.append("Сегодня по расписанию: " + "; ".join(_fmt_class(c) for c in today) + ".")

    projects = memory.cached_projects(uid)
    if projects:
        parts.append("Проекты пользователя: " + "; ".join(
            (f"{p['name']} — {p['status']}" if p.get("status") else p["name"]) for p in projects[:10]
        ) + ".")

    notes = memory.cached_notes(uid)
    if notes:
        parts.append("Недавние заметки пользователя (можешь ссылаться на них): "
                     + "; ".join(n["text"] for n in notes[:12]) + ".")

    return "\n".join(parts)
