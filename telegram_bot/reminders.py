"""Natural-language reminder parser + time helpers for the Telegram bot.

Storage and delivery live in MemoryStore (durable Postgres/SQLite) — see
add_reminder / get_due_reminders there. This module only turns Russian phrases
like «напомни через 30 минут позвонить маме» into a concrete moment in time,
respecting the user's timezone, plus small helpers to move between local time
and the UTC strings we store/compare against.
"""
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def now_utc_iso() -> str:
    """Current UTC moment as a naive ISO string (same shape we store `due` in)."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


CALL_MARK = "📞"


async def _delivery_text(r: dict, call) -> str:
    """Напоминание со звонком («📞 …») сначала пробует позвонить через ПК.
    Сообщение в чат приходит в любом случае — звонок могли не взять."""
    text = r["text"]
    if not text.startswith(CALL_MARK):
        return f"🔔 Напоминание: {text}"
    topic = text[len(CALL_MARK):].strip()
    ok, why = False, "ПК офлайн"
    if call is not None:
        try:
            ok, why = await call(r["user_id"], topic)
        except Exception as e:
            why = str(e)
    if ok:
        return f"📞 Звоню: {topic}"
    return f"🔔 Напоминание: {topic}\n(позвонить не вышло — {why})"


async def delivery_loop(bot, memory, logger, every: float = 30.0, call=None):
    """Одна доставка напоминаний на оба входа — бот и вебхук-сервер.

    Копий было две: в bot.py и в render_app.py. Сейчас они совпадали строка
    в строку, но именно так в этом проекте уже расходились пути (ради чего
    появился context_builder), и чинить пришлось бы дважды.

    Порядок важен: сначала отправка, отметка «доставлено» — после. Упавшая
    отправка (сегодня на хостинге моргал DNS) оставит напоминание в очереди
    и повторит через полминуты, а не потеряет молча.

    call(uid, topic) -> (ok, почему_нет) — звонок через ПК для «📞 …».
    """
    import asyncio
    while True:
        try:
            await asyncio.sleep(every)
            for r in await memory.get_due_reminders(now_utc_iso()):
                text = await _delivery_text(r, call)
                try:
                    await bot.send_message(chat_id=r["user_id"], text=text)
                except Exception as e:
                    logger.error(f"Reminder send: {e}")
                    # Звонок уже состоялся — повтор через полминуты позвонил
                    # бы снова. Такое напоминание считаем доставленным.
                    if not text.startswith(CALL_MARK):
                        continue
                try:
                    await memory.mark_reminder_sent(r["id"])
                except Exception as e:
                    # Доставлено, но не отмечено — иначе то же напоминание
                    # придёт снова через полминуты, и так по кругу.
                    logger.error(f"Reminder mark sent (id={r['id']}): {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Reminder loop: {e}")


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
    except Exception as exc:
        # Пользователь увидит сырое «2026-08-05T14:30:00+00:00» вместо
        # «05.08 в 19:30» — выглядит как поломка, поэтому пишем в лог.
        logger.warning("Не смог перевести время напоминания %r: %s", due_utc_iso, exc)
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
    low = text.strip().lower()

    # Cut everything up to and including the trigger word, so leading filler
    # («отлично, напомни мне …») doesn't get mistaken for the task text.
    for trg in ("напомни мне", "напомни", "remind me", "remind",
                "поставь напоминание", "таймер на", "таймер"):
        idx = low.find(trg)
        if idx != -1:
            low = low[idx + len(trg):].strip().lstrip(",").strip()
            break

    # Дата, месяц или день недели — этот быстрый разбор их не понимает и
    # раньше молча ставил «завтра»: «25 декабря в 10:00» срабатывало завтра.
    # Такие фразы отдаём Gemini (блок [[REMINDERS]] с полной датой).
    if _HAS_DATE.search(low):
        return None
    try:
        found = _find_time(low, now)
    except ValueError:
        return None          # «в 25:00» — не время; пусть разберётся Gemini
    if not found:
        return None
    when, (start, end) = found

    # «what» = the phrase minus the time expression, cleaned of filler.
    what = re.sub(r"\s+", " ", (low[:start] + " " + low[end:])).strip(" ,.")
    what = re.sub(r"^(мне|меня|что|чтобы|чтоб|о том,?\s*чтобы)\s+", "", what).strip()
    return when, (what or "напоминание")


_HAS_DATE = re.compile(
    r"послезавтра|январ|феврал|\bмарт|апрел|\bма[йя]\b|июн|июл|август|сентябр|"
    r"октябр|ноябр|декабр|понедельник|вторник|\bсред[уыа]\b|четверг|пятниц|"
    r"суббот|воскресень|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")


def _find_time(low: str, now: datetime):
    """Find a time expression ANYWHERE in `low`. Returns (when, (start, end)) or
    None. Time can be at the start or end («написать тебе через минуту»)."""
    # завтра в HH:MM
    m = re.search(r"завтра\s+в\s+(\d{1,2})[:.](\d{2})", low)
    if m:
        when = (now + timedelta(days=1)).replace(
            hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return when, m.span()

    # (сегодня) в HH:MM
    m = re.search(r"(?:сегодня\s+)?\bв\s+(\d{1,2})[:.](\d{2})", low)
    if m:
        when = now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                           second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when, m.span()

    # через N единиц  /  N единиц (после «таймер на»)
    # «через 1.5 часа» раньше читалось как «через 5 часов»: дробь не ловилась.
    m = re.search(r"(?:через\s+)?(\d+(?:[.,]\d+)?)\s*(секунд\w*|сек|минут\w*|мин|часов|часа|час\w*)", low)
    if m:
        n, unit = float(m.group(1).replace(",", ".")), m.group(2)
        if unit.startswith("сек"):
            delta = timedelta(seconds=n)
        elif unit.startswith("мин"):
            delta = timedelta(minutes=n)
        else:
            delta = timedelta(hours=n)
        return now + delta, m.span()

    # через минуту/час/полчаса/полтора часа (без цифры)
    m = re.search(r"через\s+(полтора\s+часа|полчаса|минут\w*|час\w*)", low)
    if m:
        w = m.group(1)
        if "полтора" in w:
            delta = timedelta(hours=1, minutes=30)
        elif "полчаса" in w:
            delta = timedelta(minutes=30)
        elif "минут" in w:
            delta = timedelta(minutes=1)
        else:
            delta = timedelta(hours=1)
        return now + delta, m.span()

    return None