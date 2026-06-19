"""
Proactive secretary — JARVIS reaches out on its own.

• Morning briefing (~08:00 local): greeting, today's plan, a motivating push.
• Evening review  (~22:00 local): what was on the plan, set up tomorrow.

Per-user local time comes from user_context (phone-reported tz) with the
configured timezone as fallback. Each briefing is sent at most once per day per
user; the "already sent" marker is persisted in the durable meta table, so a
restart won't double-send.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from telegram_bot import user_context
from telegram_bot import agenda
from telegram_bot import weather

logger = logging.getLogger("jarvis-proactive")

# Local-hour windows. If the bot is offline during the whole window that day's
# briefing is simply skipped (no late-night surprise messages).
_MORNING = range(8, 11)    # 08:00–10:59
_EVENING = range(22, 24)   # 22:00–23:59

_CHECK_EVERY = 60  # seconds


def _today_tasks(tasks: list) -> list:
    return [t for t in tasks if t.get("due") and agenda.is_today(t["due"])]


def _tasks_text(tasks: list) -> str:
    if not tasks:
        return "（на сегодня задач не записано）"
    lines = []
    for t in tasks:
        due = t.get("due")
        when = f" в {datetime.fromisoformat(due).strftime('%H:%M')}" if due and \
            (datetime.fromisoformat(due).hour or datetime.fromisoformat(due).minute) else ""
        lines.append(f"- {t['title']}{when}")
    return "\n".join(lines)


def _classes_text(classes: list) -> str:
    if not classes:
        return ""
    items = "; ".join(
        ((f"{c['time']} " if c.get('time') else "") + c['subject']
         + (f" ({c['location']})" if c.get('location') else "")) for c in classes
    )
    return f"Пары/занятия сегодня: {items}\n\n"


def _habits_text(undone: list) -> str:
    if not undone:
        return ""
    return "Привычки, ещё не отмеченные сегодня: " + ", ".join(h['title'] for h in undone) + "\n\n"


def _reminders_text(reminders: list) -> str:
    if not reminders:
        return ""
    return "Ближайшие напоминания: " + "; ".join(r['text'] for r in reminders[:3]) + "\n\n"


def _morning_prompt(name: str, today: list, all_open: list, weather_line: str = "",
                    classes: list = None, undone_habits: list = None, reminders: list = None) -> str:
    who = f" Пользователя зовут {name}." if name else ""
    wx = f"Погода сегодня: {weather_line}.\n\n" if weather_line else ""
    return (
        "Сейчас утро. Составь короткий тёплый утренний брифинг для пользователя "
        "в своём текущем стиле (учитывай режим личности и что ты о нём знаешь)."
        f"{who}\n\n"
        f"{wx}"
        f"{_classes_text(classes or [])}"
        f"Задачи и события на сегодня:\n{_tasks_text(today)}\n\n"
        f"{_reminders_text(reminders or [])}"
        f"{_habits_text(undone_habits or [])}"
        f"Всего открытых задач: {len(all_open)}.\n\n"
        "Поздоровайся по-человечески; если есть пары — назови их, упомяни погоду, "
        "кратко проговори план дня, мягко напомни про непройденные привычки и "
        "ближайшие напоминания (если есть), добавь одну искреннюю мотивирующую мысль. "
        "Без воды, 4–8 строк, эмодзи ок."
    )


def _tomorrow_classes_text(classes: list) -> str:
    if not classes:
        return ""
    items = []
    for c in classes:
        s = (f"{c['time']} " if c.get("time") else "") + c["subject"]
        if c.get("location"):
            s += f" ({c['location']})"
        items.append(s)
    return "Завтра по расписанию: " + "; ".join(items) + "\n\n"


def _evening_prompt(name: str, today: list, all_open: list,
                    tomorrow_classes: list = None, undone_habits: list = None,
                    tomorrow_reminders: list = None) -> str:
    who = f" Пользователя зовут {name}." if name else ""
    tr = ""
    if tomorrow_reminders:
        tr = "Напоминания на завтра: " + "; ".join(r['text'] for r in tomorrow_reminders[:3]) + "\n\n"
    return (
        "Сейчас вечер. Составь короткий, но содержательный вечерний разбор дня "
        "для пользователя в своём текущем стиле."
        f"{who}\n\n"
        f"Что было запланировано на сегодня:\n{_tasks_text(today)}\n\n"
        f"Открытых задач сейчас: {len(all_open)}.\n\n"
        f"{_tomorrow_classes_text(tomorrow_classes or [])}"
        f"{tr}"
        f"{_habits_text(undone_habits or [])}"
        "Спроси по-доброму, как прошёл день, отметь что можно закрыть. "
        "Если есть непройденные сегодня привычки — мягко спроси, удалось ли. "
        "Если на завтра есть пары/напоминания — кратко проговори их, чтобы человек "
        "подготовился. Предложи наметить 1–3 главные задачи на завтра. "
        "Тепло и по делу, 4–7 строк, можно эмодзи."
    )


async def _send_briefing(bot, gemini, memory, uid: int, slot: str,
                         default_tz: str, default_city: str = ""):
    profile = await memory.get_profile(uid)
    tasks = await memory.get_tasks(uid)
    today = _today_tasks(tasks)
    name = profile.get("name", "")
    if slot == "morning":
        city = user_context.get_city(uid, default_city)
        weather_line = await weather.for_city(city) if city else None
        now = user_context.local_now(uid, default_tz)
        classes = await memory.schedule_for_day(uid, now.weekday())
        habits = await memory.get_habits(uid, now.date().isoformat())
        undone_habits = [h for h in habits if not h.get("done_today")]
        reminders = await memory.list_reminders(uid)
        prompt = _morning_prompt(name, today, tasks, weather_line or "",
                                 classes, undone_habits, reminders)
        header = "☀️"
    else:
        now = user_context.local_now(uid, default_tz)
        tomorrow_wd = (now.weekday() + 1) % 7
        tomorrow_classes = await memory.schedule_for_day(uid, tomorrow_wd)
        habits = await memory.get_habits(uid, now.date().isoformat())
        undone_habits = [h for h in habits if not h.get("done_today")]
        tomorrow_str = (now.date() + timedelta(days=1)).isoformat()
        reminders = await memory.list_reminders(uid)
        tomorrow_reminders = [r for r in reminders if str(r.get("due", "")).startswith(tomorrow_str)]
        prompt = _evening_prompt(name, today, tasks, tomorrow_classes,
                                 undone_habits, tomorrow_reminders)
        header = "🌙"
    try:
        text = await gemini.generate_once(uid, prompt)
    except Exception as e:
        logger.error(f"briefing gen for {uid}: {e}")
        return False
    try:
        await bot.send_message(chat_id=uid, text=f"{header} {text}")
        return True
    except Exception as e:
        logger.warning(f"briefing send to {uid}: {e}")
        return False


async def tick(bot, gemini, memory, default_tz: str, default_city: str = ""):
    """One scheduler pass — send any due briefings."""
    try:
        uids = await memory.all_user_ids()
    except Exception as e:
        logger.debug(f"all_user_ids: {e}")
        return
    for uid in uids:
        now = user_context.local_now(uid, default_tz)
        today_key = now.strftime("%Y-%m-%d")
        for slot, window in (("morning", _MORNING), ("evening", _EVENING)):
            if now.hour not in window:
                continue
            marker = f"briefing_{slot}"
            if await memory.get_meta(uid, marker) == today_key:
                continue  # already sent today
            sent = await _send_briefing(bot, gemini, memory, uid, slot,
                                        default_tz, default_city)
            # Mark even on send-failure to avoid hammering a blocked chat all day
            await memory.set_meta(uid, marker, today_key)
            if sent:
                logger.info(f"Sent {slot} briefing to {uid}")


async def loop(bot, gemini, memory, default_tz: str = "Asia/Almaty", default_city: str = ""):
    logger.info("Proactive secretary loop started ✅")
    while True:
        try:
            await asyncio.sleep(_CHECK_EVERY)
            await tick(bot, gemini, memory, default_tz, default_city)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"proactive loop: {e}")
