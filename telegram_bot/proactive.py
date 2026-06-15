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
from datetime import datetime

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


def _morning_prompt(name: str, today: list, all_open: list, weather_line: str = "") -> str:
    who = f" Пользователя зовут {name}." if name else ""
    wx = f"Погода сегодня: {weather_line}.\n\n" if weather_line else ""
    return (
        "Сейчас утро. Составь короткий тёплый утренний брифинг для пользователя "
        "в своём текущем стиле (учитывай режим личности и что ты о нём знаешь)."
        f"{who}\n\n"
        f"{wx}"
        f"Задачи и события на сегодня:\n{_tasks_text(today)}\n\n"
        f"Всего открытых задач: {len(all_open)}.\n\n"
        "Поздоровайся по-человечески, упомяни погоду если она есть, кратко "
        "проговори план на день (если задач нет — мягко предложи задать цель), "
        "добавь одну искреннюю мотивирующую мысль. Без воды, 3–6 строк, эмодзи ок."
    )


def _evening_prompt(name: str, today: list, all_open: list) -> str:
    who = f" Пользователя зовут {name}." if name else ""
    return (
        "Сейчас вечер. Составь короткий вечерний разбор дня для пользователя "
        "в своём текущем стиле."
        f"{who}\n\n"
        f"Что было запланировано на сегодня:\n{_tasks_text(today)}\n\n"
        f"Открытых задач сейчас: {len(all_open)}.\n\n"
        "Спроси по-доброму, как прошёл день, отметь, что можно закрыть, "
        "и предложи коротко наметить 1–3 главные задачи на завтра. "
        "Тепло и по делу, 3–6 строк, можно эмодзи."
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
        prompt = _morning_prompt(name, today, tasks, weather_line or "")
        header = "☀️"
    else:
        prompt = _evening_prompt(name, today, tasks)
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
