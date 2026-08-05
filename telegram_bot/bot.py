#!/usr/bin/env python3
"""JARVIS Telegram Bot — mobile interface, runs on VPS."""
import asyncio
import base64
import contextlib
import logging
import re
import sys
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_bot.config import load as load_config
from telegram_bot.gemini_client import GeminiClient
from telegram_bot.pc_bridge import PCBridge
from telegram_bot import reminders as rem
from telegram_bot.reminders import parse_reminder

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("jarvis-bot")

from telegram_bot import user_context
from telegram_bot import personas
from telegram_bot import agenda
from telegram_bot import proactive
from telegram_bot import onboarding
from telegram_bot import directives
from telegram_bot import context_builder
from telegram_bot import keywords
from telegram_bot import voice
from telegram_bot import recall
from telegram_bot import gcal
from telegram_bot import curiosity
from telegram_bot import memory_rag
from telegram_bot.memory_store import MemoryStore

cfg = load_config()
gemini = GeminiClient(cfg.gemini_api_key, cfg.gemini_model)
bridge = PCBridge()
memory = MemoryStore()


_WD_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


# Формат записи пары — общий с context_builder, чтобы копии не разошлись.
_fmt_class = context_builder.fmt_class


def _build_context(uid: int) -> str:
    # Single source of truth — shared with render_app.py (see context_builder.py).
    return context_builder.build_context(memory, cfg, uid)


gemini.set_context_provider(_build_context)
gemini.set_recall_provider(memory_rag.make_recall_provider(memory, gemini, recall))

_BOT_COMMANDS = [
    BotCommand("start",      "Запустить JARVIS"),
    BotCommand("help",       "Список команд"),
    BotCommand("app",        "Открыть Mini App"),
    BotCommand("status",     "Статус ПК"),
    BotCommand("pc",         "Команда на ПК"),
    BotCommand("screenshot", "Скриншот рабочего стола"),
    BotCommand("camera",     "Снимок с веб-камеры"),
    BotCommand("vol",        "Громкость ПК: /vol 70"),
    BotCommand("lock",       "Заблокировать экран"),
    BotCommand("sysinfo",    "Состояние системы"),
    BotCommand("briefing",   "Утренний брифинг"),
    BotCommand("remind",     "Добавить напоминание"),
    BotCommand("reminders",  "Мои напоминания"),
    BotCommand("task",       "Добавить задачу/событие"),
    BotCommand("tasks",      "Список задач"),
    BotCommand("today",      "Сегодня: пары + задачи"),
    BotCommand("schedule",   "Расписание пар на неделю"),
    BotCommand("clearschedule", "Очистить расписание"),
    BotCommand("projects",   "Статусы моих проектов"),
    BotCommand("delproject", "Удалить проект: /delproject имя"),
    BotCommand("done",       "Закрыть задачу по номеру"),
    BotCommand("habit",      "Добавить привычку"),
    BotCommand("habits",     "Мои привычки и серии"),
    BotCommand("check",      "Отметить привычку за сегодня"),
    BotCommand("morning",    "Утренний брифинг сейчас"),
    BotCommand("evening",    "Вечерний разбор сейчас"),
    BotCommand("notes",      "Мои заметки (входящая)"),
    BotCommand("note",       "Записать заметку: /note текст"),
    BotCommand("delnote",    "Удалить заметку: /delnote номер"),
    BotCommand("findnote",   "Найти в заметках: /findnote слово"),
    BotCommand("contacts",   "Кому можно писать (белый список)"),
    BotCommand("addcontact", "Добавить контакт: /addcontact имя @user"),
    BotCommand("delcontact", "Удалить контакт: /delcontact имя"),
    BotCommand("mode",       "Режим личности (ментор/друг/бизнес)"),
    BotCommand("profile",    "Что JARVIS обо мне знает"),
    BotCommand("memstats",   "Состояние памяти"),
    BotCommand("journal",    "Дневник дня"),
    BotCommand("reindex",    "Проиндексировать историю (поиск)"),
    BotCommand("ask",        "Пусть JARVIS спросит обо мне"),
    BotCommand("curiosity",  "Вкл/выкл вопросы обо мне"),
    BotCommand("remember",   "Запомнить факт обо мне"),
    BotCommand("forget",     "Стереть всё обо мне"),
    BotCommand("clear",      "Очистить историю диалога"),
]

_PC_KEYWORDS = [
    # Music
    "play", "stop", "pause", "next", "prev",
    "включи", "выключи", "стоп", "пауза", "следующий", "предыдущий", "трек",
    "поставь", "запусти", "воспроизведи", "играй",
    "переключи", "отключи", "громче", "тише", "дальше", "громкость",
    # Apps & browser
    "open", "открой",
    # Weather
    "weather", "погода",
    # Search
    "search", "найди", "поищи",
    # Window control
    "сверни", "свернуть", "minimize", "рабочий стол", "разверни",
    "закрой окно", "переключи окно", "проводник", "диспетчер",
    # Camera
    "камер", "вебкам", "webcam", "сфоткай", "что рядом", "что вокруг", "что там происходит",
    # System
    "screenshot", "скриншот",
    "заблокируй", "заблокировать",
    "выключи компьютер", "выключи пк",
    "перезагрузи компьютер",
    # Keyboard input
    "разблокир", "нажми enter", "нажать enter", "нажми интер",
]

_REMINDER_TRIGGERS = ["напомни", "remind me", "поставь напоминание", "таймер на"]

# ── JARVIS Outbound: send to whitelisted contacts (executed by PC userbot) ──────
# JARVIS itself decides to send and composes the message, emitting a [[SEND]]
# block (parsed by _apply_send_directives) — same agentic pattern as reminders.
_pending_sends: dict = {}        # token -> asyncio.Task (5s undo window)
_ub_counter = 0


async def _dispatch_outbound(token, target, alias, message, as_voice, user_id, sent_msg):
    """After the 5s undo window, hand the message to the PC userbot via the bridge."""
    try:
        await asyncio.sleep(5)
    except asyncio.CancelledError:
        return
    _pending_sends.pop(token, None)
    res = await bridge.send_userbot(target, message, as_voice, user_id)
    txt = (res or {}).get("text", "")
    try:
        if res and "Отправлено" in txt:
            kind = "🎤 голосом" if as_voice else "✍️ текстом"
            await sent_msg.edit_text(f"✅ Отправлено {alias} ({kind})")
        else:
            await sent_msg.edit_text(f"❌ Не отправлено {alias}: {txt or 'ПК офлайн / не ответил'}")
    except Exception as e:
        logger.debug(f"outbound edit: {e}")


# ── Action chains: one bounded FETCH step ───────────────────────────────────
# JARVIS may need a real PC result BEFORE finishing a task. It emits a [[FETCH]]
# block; we run ONE safe read-only command, feed the result back, and let it
# complete. Bounded to a single round — loop-safe and quota-safe (max +1 call).
async def _persist_exchange(uid: int, user_text: str, reply: str):
    """Log the raw user+assistant exchange to the durable message log, and index
    the user's message for semantic recall. Fire-and-forget — never blocks the
    reply path."""
    try:
        await memory.add_message(uid, "user", user_text)
        if reply and reply.strip():
            await memory.add_message(uid, "model", reply)
        await memory_rag.index(memory, gemini, uid, "message", user_text)
    except Exception as e:
        logger.debug(f"persist exchange: {e}")


_RE_FETCH = re.compile(r"\[\[FETCH\]\](.*?)\[\[/FETCH\]\]", re.S | re.I)
_FETCH_UNSAFE = ("выключ", "выруб", "shutdown", "перезагруз", "reboot",
                 "заблокир", "lock", "удал", "delete", "снеси",
                 "громкост", "volume", "заверши процесс", "kill")


def _fetch_is_safe(cmd: str) -> bool:
    """Only read/info commands may auto-run in a chain — destructive ones must
    go through the normal explicit path."""
    low = cmd.lower()
    return not any(w in low for w in _FETCH_UNSAFE)


async def _resolve_fetch(user_id: int, reply: str) -> str:
    """If JARVIS asked for a PC result via [[FETCH]], run ONE safe command and
    re-prompt once with the result so it can finish. Returns the final reply."""
    m = _RE_FETCH.search(reply)
    if not m:
        return reply
    cmd = m.group(1).strip().lstrip("-•*").strip()
    clean = _RE_FETCH.sub("", reply).strip()
    if not cmd:
        return clean
    if not bridge.connected:
        return clean or "ПК сейчас офлайн — не смог выполнить шаг на компьютере."
    if not _fetch_is_safe(cmd):
        logger.warning(f"FETCH blocked unsafe command: {cmd!r}")
        return clean or "Этот шаг я не выполняю автоматически — сделай его обычной командой."
    pc_result = await bridge.send_command(cmd, user_id)
    followup = (
        f"Результат шага на ПК для команды «{cmd}»:\n{pc_result or '(нет ответа от ПК)'}\n\n"
        "Заверши задачу пользователя, опираясь на этот результат. "
        "НЕ используй блок [[FETCH]] снова."
    )
    return await gemini.chat(user_id, followup)


_RE_SEND = re.compile(r"\[\[SEND\]\](.*?)\[\[/SEND\]\]", re.S | re.I)


async def _apply_send_directives(update: Update, user_id: int, reply: str) -> str:
    """Parse [[SEND]] blocks JARVIS composed, stage each with a 5s undo window,
    and strip the block from the visible reply. Returns the clean reply."""
    block = _RE_SEND.search(reply)
    clean = _RE_SEND.sub("", reply).strip()
    if not block:
        return clean

    staged = failed = 0
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|", 2)]
        if len(parts) < 3:
            continue
        alias_raw, mode, message = parts[0], parts[1].lower(), parts[2]
        if not message:
            continue
        target = await memory.resolve_contact(user_id, alias_raw)
        if not target:
            await update.effective_message.reply_text(
                f"⚠️ «{alias_raw}» не в белом списке. Добавь: /addcontact {alias_raw.lower()} @username"
            )
            failed += 1
            continue
        as_voice = any(w in mode for w in ("voice", "голос", "audio", "аудио"))
        if not bridge.connected:
            await memory.queue_outbound(user_id, target, alias_raw, message, as_voice)
            kind = "🎤 голосом" if as_voice else "текстом"
            await update.effective_message.reply_text(
                f"📭 ПК офлайн — поставил в очередь для {alias_raw} ({kind}).\n"
                f"Отправлю автоматически, как только ПК включится."
            )
            failed += 1     # suppress JARVIS's optimistic "передаю" text
            continue
        global _ub_counter
        _ub_counter += 1
        token = f"{user_id}_{_ub_counter}"
        kind = " 🎤 голосом" if as_voice else ""
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✖ Отменить", callback_data=f"ubcancel:{token}")]])
        sent = await update.effective_message.reply_text(
            f"✉️ {alias_raw}{kind}:\n«{message}»\n\nОтправлю через 5 сек… (тапни «Отменить», чтобы остановить)",
            reply_markup=kb,
        )
        _pending_sends[token] = asyncio.create_task(
            _dispatch_outbound(token, target, alias_raw, message, as_voice, user_id, sent)
        )
        staged += 1

    # Don't show JARVIS's optimistic "передаю…" text if nothing actually got
    # staged (PC offline / contact not whitelisted) — only the clear error.
    if failed and not staged:
        return ""
    return clean


def _is_authorized(update: Update) -> bool:
    if not cfg.allowed_user_ids:
        return True
    return update.effective_user.id in cfg.allowed_user_ids


def _looks_like_pc_command(text: str) -> bool:
    # По началу слова, а не подстрокой: «чистоплотный» содержит «стоп»
    # и однажды увёл личное сообщение на компьютер (см. keywords.py).
    return keywords.matches(text, _PC_KEYWORDS)


def _looks_like_reminder(text: str) -> bool:
    # Only a CLEAN reminder command (starts with a trigger) takes the fast path.
    # A reminder word buried in a longer brain-dump must fall through to the
    # unified inbox (Gemini), which sorts reminders + tasks + notes together.
    low = text.strip().lower()
    return any(low.startswith(k) for k in _REMINDER_TRIGGERS)


_VOICE_REQUEST = (
    "голосом", "вслух", "озвучь", "скажи голосом", "ответь голосом",
    "голосовым", "войсом", "voice",
)


def _wants_voice_reply(text: str) -> bool:
    """Просил ли пользователь ответить голосом прямо в этом сообщении."""
    return keywords.matches(text, _VOICE_REQUEST)


async def _reply_pc_result(message, result: dict | None) -> None:
    """Ответ на команду ПК — с картинкой, если ПК её прислал.

    Раньше естественные формулировки («сделай скриншот», «покажи камеру») шли
    через send_command, который отдаёт только текст: снимок делался, приходило
    «🖥 Скриншот ✅», а само изображение молча выбрасывалось. Картинку видели
    только слэш-команды /screenshot и /camera.
    """
    if result is None:
        await message.reply_text(
            "❌ ПК не ответил. Убедись что pc_server запущен и попробуй ещё раз."
        )
        return
    text = result.get("text") or "Готово"
    if result.get("image_b64"):
        try:
            await message.reply_photo(
                base64.b64decode(result["image_b64"]), caption=_prefixed(text, "📸")
            )
            return
        except Exception as e:
            logger.warning("Не смог отправить снимок с ПК: %s", e)
    await message.reply_text(_prefixed(text, "🖥"))


def _prefixed(text: str, icon: str) -> str:
    """Значок в начале — только если ПК не поставил свой.

    Иначе выходило «📸 📷 Снимок с камеры» и «🖥 🖥 Состояние системы»:
    ответы с ПК приходят уже со своим значком.
    """
    first = text.lstrip()[:1]
    return text if first and not first.isalnum() and not first.isspace() else f"{icon} {text}"


def _app_keyboard() -> InlineKeyboardMarkup | None:
    if not cfg.miniapp_url:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬡ Открыть JARVIS", web_app=WebAppInfo(url=cfg.miniapp_url))
    ]])


# ── Basic commands ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    name = update.effective_user.first_name or "сэр"
    pc = "онлайн ✅" if bridge.connected else "офлайн ❌"
    uid = update.effective_user.id
    await memory.ensure_loaded(uid)
    await update.effective_message.reply_text(
        f"Привет, {name}! Я JARVIS — твой личный ИИ-ассистент.\n\n"
        f"🖥 ПК: {pc}\n"
        f"🤖 Gemini: готов ✅\n\n"
        f"Напиши что угодно — поговорим, помогу, отвечу.\n"
        f"/help — все команды",
        reply_markup=_app_keyboard(),
    )
    # First meeting → get to know the user
    if not await onboarding.already_onboarded(memory, uid):
        await update.effective_message.reply_text(onboarding.start(uid))


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    pc = "онлайн ✅" if bridge.connected else "офлайн ❌"
    await update.effective_message.reply_text(
        f"📋 *Команды JARVIS*\n\n"
        f"*ПК* ({pc})\n"
        f"`/pc <команда>` — любая команда\n"
        f"`/screenshot` — скриншот рабочего стола\n"
        f"`/camera` — снимок с веб-камеры\n"
        f"`/vol 70` — установить громкость 70%\n"
        f"`/lock` — заблокировать экран\n"
        f"`/sysinfo` — CPU, RAM, батарея\n"
        f"`/briefing` — утренний брифинг\n\n"
        f"*Примеры команд:*\n"
        f"`/pc поставь believer`\n"
        f"`/pc переключи музыку`\n"
        f"`/pc стоп`\n"
        f"`/pc погода в Ташкенте`\n"
        f"`/pc сверни все окна`\n"
        f"`/pc открой chrome`\n"
        f"`/pc найди новости AI`\n\n"
        f"*Напоминания*\n"
        f"`/remind через 30 минут позвонить`\n"
        f"`/remind завтра в 9:00 встреча`\n"
        f"`/reminders` — список\n\n"
        f"*Задачи и календарь*\n"
        f"`/task завтра в 9:00 созвон`\n"
        f"`/task купить хлеб` · `/tasks` · `/today`\n"
        f"`/done 2` — закрыть задачу №2\n\n"
        f"*Привычки*\n"
        f"`/habit пить воду` · `/habits` · `/check 1`\n\n"
        f"*Проактивный секретарь*\n"
        f"Сам пишу утром (8:00) и вечером (22:00)\n"
        f"`/morning` · `/evening` — брифинг сейчас\n\n"
        f"*Память (я тебя помню)*\n"
        f"`/profile` — что я о тебе знаю\n"
        f"`/remember меня зовут Сардор`\n"
        f"`/forget` — стереть всё обо мне\n\n"
        f"*Личность*\n"
        f"`/mode друг` · `/mode ментор` · `/mode бизнес`\n"
        f"`/mode` — список и текущий режим\n\n"
        f"*Прочее*\n"
        f"`/status` — статус ПК\n"
        f"`/clear` — очистить историю\n"
        f"`/app` — голосовой интерфейс\n\n"
        f"💬 Или просто пиши — я твой ассистент!",
        parse_mode="Markdown",
    )


async def cmd_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    kb = _app_keyboard()
    if kb:
        await update.effective_message.reply_text("Открываю JARVIS ↓", reply_markup=kb)
    else:
        await update.effective_message.reply_text(
            "Mini App не настроен.\n"
            "Добавь miniapp_url в config/api_keys.json и перезапусти бота."
        )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    pc = "онлайн ✅" if bridge.connected else "офлайн ❌"
    await update.effective_message.reply_text(f"🖥 ПК: {pc}\n🤖 Gemini: готов ✅")


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    gemini.clear_history(update.effective_user.id)
    await update.effective_message.reply_text("История диалога очищена ✅")


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    profile = await memory.get_profile(uid)
    facts = await memory.get_facts(uid)
    lines = ["🧠 *Что я о тебе знаю*\n"]
    if profile.get("name"):
        lines.append(f"*Имя:* {profile['name']}")
    if profile.get("about"):
        lines.append(f"*О тебе:* {profile['about']}")
    if profile.get("goals"):
        lines.append(f"*Цели:* {profile['goals']}")
    if profile.get("preferences"):
        lines.append(f"*Предпочтения:* {profile['preferences']}")
    if facts:
        lines.append("\n*Факты:*\n" + "\n".join(f"• {f}" for f in facts[-30:]))
    if len(lines) == 1:
        lines.append("Пока ничего. Просто общайся со мной — я запоминаю сам. "
                     "Или напиши `/remember <факт>`.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_memstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Read-only memory audit: what JARVIS actually has stored about you."""
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    s = await memory.stats(uid)
    c = s["counts"]
    lines = [
        "📊 *Состояние памяти*\n",
        f"💾 Хранилище: *{s['backend']}*"
        + (f" ⚠️ АВАРИЙНЫЙ РЕЖИМ: основная база не отвечает ({s['degraded_reason']}). "
           "Записи этого периода в неё не попадут."
           if s["degraded"]
           else "" if s["backend"] == "Postgres"
           else " ⚠️ (эфемерно — задай DATABASE_URL!)"),
        f"💬 Сообщений в логе: *{c.get('messages', 0)}*",
        f"🧠 В семантической памяти: *{c.get('embeddings', 0)}* "
        + ("(/reindex чтобы доиндексировать историю)" if c.get('embeddings', 0) < c.get('messages', 0) else "✓"),
        f"🧠 Фактов: *{s['facts_total']}*"
        + (f" (в контекст идёт {s['facts_in_context']})"
           if s['facts_total'] > s['facts_in_context'] else ""),
        f"📝 Заметок: {c.get('notes', 0)}   ✅ Задач: {c.get('tasks', 0)}   "
        f"🔁 Привычек: {c.get('habits', 0)}",
        f"📚 Пар: {c.get('schedule', 0)}   💻 Проектов: {c.get('projects', 0)}   "
        f"👥 Контактов: {c.get('contacts', 0)}   🔔 Напоминаний: {c.get('reminders', 0)}",
    ]
    if s["profile_filled"]:
        lines.append(f"\n👤 Профиль заполнен: {', '.join(s['profile_filled'])}")
    if s["facts_total"] > s["facts_in_context"]:
        lines.append(
            f"\n⚠️ {s['facts_total'] - s['facts_in_context']} старых фактов хранятся, "
            "но НЕ попадают в контекст (лимит). Фаза 2 это исправит.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_journal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show the recent daily journal (life-log) entries JARVIS wrote."""
    if not _is_authorized(update):
        return
    entries = await memory.list_journal(update.effective_user.id, limit=14)
    if not entries:
        await update.effective_message.reply_text(
            "📔 Дневник пока пуст. Я пишу короткую запись каждый вечер — "
            "появится после вечернего разбора (или вызови /evening).")
        return
    lines = ["📔 *Твой дневник*\n"]
    for e in entries:
        lines.append(f"*{e['day']}*\n{e['text']}\n")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_reindex(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Backfill semantic embeddings for the existing message/fact history."""
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    await update.effective_message.reply_text(
        "🧠 Индексирую твою историю для семантического поиска… это в фоне, пришлю итог.")

    async def _run():
        try:
            n = await memory_rag.backfill(memory, gemini, uid, limit=600)
            total = await memory.embedding_count(uid)
            await ctx.bot.send_message(
                uid, f"✅ Готово. Новых проиндексировано: {n}. "
                     f"Всего в семантической памяти: {total}.")
        except Exception as e:
            await ctx.bot.send_message(uid, f"❌ Индексация прервалась: {e}")

    asyncio.create_task(_run())


async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """On-demand: JARVIS asks the user a get-to-know-you question now."""
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    await memory.ensure_loaded(uid)
    q = await curiosity.pose(memory, uid)
    if q:
        await update.effective_message.reply_text(f"💭 {q}")
    else:
        done, total = await curiosity.progress(memory, uid)
        await update.effective_message.reply_text(
            f"Я уже расспросил тебя обо всём из своего списка ({done}/{total}) 🙂 "
            "Но рассказывай что угодно — я всё запоминаю.")


async def cmd_curiosity(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle the daily get-to-know-you questions on/off."""
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    off = (await memory.get_meta(uid, "curio_off")) == "1"
    if off:
        await memory.set_meta(uid, "curio_off", "")
        await update.effective_message.reply_text(
            "🔔 Снова буду иногда расспрашивать тебя о тебе (раз в день, днём).")
    else:
        await memory.set_meta(uid, "curio_off", "1")
        await update.effective_message.reply_text(
            "🔕 Ок, больше не буду задавать вопросы сам. Включить обратно: /curiosity")


async def cmd_remember(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    fact = " ".join(ctx.args).strip()
    if not fact:
        await update.effective_message.reply_text(
            "Что запомнить?\nПример: `/remember я живу в Шымкенте`\n"
            "`/remember меня зовут Сардор`", parse_mode="Markdown"
        )
        return
    uid = update.effective_user.id
    low = fact.lower()
    # Smart routing into dossier fields
    if low.startswith(("меня зовут", "я ")) and "зовут" in low:
        name = fact.split("зовут", 1)[1].strip()
        await memory.set_profile_field(uid, "name", name)
        await update.effective_message.reply_text(f"Запомнил, тебя зовут {name} ✅")
        return
    added = await memory.add_fact(uid, fact)
    await update.effective_message.reply_text(
        "Запомнил ✅" if added else "Я это уже знаю 🙂"
    )


def _mode_keyboard() -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(personas.label(mid), callback_data=f"mode:{mid}")
            for mid in personas.PERSONAS]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    return InlineKeyboardMarkup(rows)


def _habits_keyboard(habits: list) -> InlineKeyboardMarkup | None:
    if not habits:
        return None
    rows = [[InlineKeyboardButton(
        f"{'✅' if h['done_today'] else '⬜'} {h['title']}",
        callback_data=f"hab:{h['id']}")] for h in habits]
    return InlineKeyboardMarkup(rows)


def _tasks_keyboard(tasks: list) -> InlineKeyboardMarkup | None:
    if not tasks:
        return None
    rows = [[InlineKeyboardButton(f"✓ {t['title'][:40]}", callback_data=f"task:{t['id']}")]
            for t in tasks]
    return InlineKeyboardMarkup(rows)


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _is_authorized(update):
        await q.answer("Нет доступа", show_alert=True)
        return
    uid = q.from_user.id
    data = q.data or ""
    try:
        if data.startswith("ubcancel:"):
            token = data.split(":", 1)[1]
            task = _pending_sends.pop(token, None)
            if task:
                task.cancel()
                await q.answer("Отменено")
                await q.edit_message_text("✖ Отменено — ничего не отправлено.")
            else:
                await q.answer("Уже отправлено")
            return
        if data.startswith("mode:"):
            mid = data.split(":", 1)[1]
            await memory.set_mode(uid, mid)
            await q.answer(f"Режим: {personas.label(mid)}")
            await q.edit_message_text(
                f"Готово, теперь я в режиме: {personas.label(mid)} ✅",
                reply_markup=_mode_keyboard())
        elif data.startswith("hab:"):
            today = _today_str(uid)
            await memory.toggle_habit(uid, int(data.split(":", 1)[1]), today)
            habits = await memory.get_habits(uid, today)
            await q.answer("Отмечено 🔥")
            await q.edit_message_text(_render_habits(habits), parse_mode="Markdown",
                                      reply_markup=_habits_keyboard(habits))
        elif data.startswith("task:"):
            await memory.complete_task(uid, int(data.split(":", 1)[1]))
            tasks = await memory.get_tasks(uid)
            await q.answer("Закрыто ✅")
            await q.edit_message_text(agenda.render_list(tasks), parse_mode="Markdown",
                                      reply_markup=_tasks_keyboard(tasks))
        else:
            await q.answer()
    except Exception as e:
        logger.error(f"on_callback '{data}': {e}")
        await q.answer("Не получилось 😕", show_alert=False)


def _today_str(uid: int) -> str:
    return user_context.local_now(uid, cfg.timezone).date().isoformat()


def _render_habits(habits: list) -> str:
    if not habits:
        return ("Привычек пока нет 🌱\nДобавь: `/habit пить воду`, "
                "`/habit читать 20 минут`")
    lines = ["🔁 *Твои привычки*\n"]
    for i, h in enumerate(habits, 1):
        box = "✅" if h["done_today"] else "⬜"
        streak = f" 🔥{h['streak']}" if h["streak"] else ""
        lines.append(f"{i}. {box} {h['title']}{streak}")
    lines.append("\nОтметить за сегодня: `/check <номер>`")
    return "\n".join(lines)


async def cmd_habit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    title = " ".join(ctx.args).strip()
    if not title:
        await update.effective_message.reply_text(
            "Какую привычку добавить?\n`/habit пить воду`\n`/habit спорт`\n"
            "`/habit читать 20 минут`", parse_mode="Markdown")
        return
    await memory.add_habit(update.effective_user.id, title)
    await update.effective_message.reply_text(
        f"Добавил привычку 🌱 *{title}*\nОтмечай каждый день — соберём серию 🔥",
        parse_mode="Markdown")


async def cmd_habits(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    habits = await memory.get_habits(uid, _today_str(uid))
    await update.effective_message.reply_text(
        _render_habits(habits), parse_mode="Markdown",
        reply_markup=_habits_keyboard(habits))


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    arg = " ".join(ctx.args).strip()
    if not arg.isdigit():
        await update.effective_message.reply_text(
            "Укажи номер привычки из `/habits`. Пример: `/check 1`", parse_mode="Markdown")
        return
    uid = update.effective_user.id
    today = _today_str(uid)
    habits = await memory.get_habits(uid, today)
    n = int(arg)
    if n < 1 or n > len(habits):
        await update.effective_message.reply_text(f"Нет привычки №{n}. Список: /habits")
        return
    h = habits[n - 1]
    done = await memory.toggle_habit(uid, h["id"], today)
    if done:
        new_streak = h["streak"] + (0 if h["done_today"] else 1)
        await update.effective_message.reply_text(
            f"✅ «{h['title']}» отмечено! Серия: 🔥{new_streak}")
    else:
        await update.effective_message.reply_text(f"↩️ Снял отметку с «{h['title']}»")


async def cmd_morning(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.effective_message.chat.send_action("typing")
    await proactive._send_briefing(
        ctx.bot, gemini, memory, update.effective_user.id, "morning",
        cfg.timezone, cfg.default_city
    )


async def cmd_evening(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.effective_message.chat.send_action("typing")
    await proactive._send_briefing(
        ctx.bot, gemini, memory, update.effective_user.id, "evening",
        cfg.timezone, cfg.default_city
    )


async def cmd_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = " ".join(ctx.args).strip()
    if not text:
        await update.effective_message.reply_text(
            "Что добавить?\n"
            "`/task купить хлеб`\n"
            "`/task завтра в 9:00 созвон с клиентом`\n"
            "`/task 25.06 сдать отчёт`",
            parse_mode="Markdown",
        )
        return
    uid = update.effective_user.id
    due, title = agenda.parse(text)
    await memory.add_task(uid, title, due)
    if due:
        await update.effective_message.reply_text(
            f"Записал 🗓 *{title}* — {agenda.fmt_due(due)}", parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text(
            f"Добавил в список ☐ *{title}*", parse_mode="Markdown"
        )


async def cmd_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    tasks = await memory.get_tasks(update.effective_user.id)
    await update.effective_message.reply_text(
        agenda.render_list(tasks), parse_mode="Markdown",
        reply_markup=_tasks_keyboard(tasks)
    )


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    wd = user_context.local_now(uid, cfg.timezone).weekday()
    classes = await memory.schedule_for_day(uid, wd)
    tasks = [t for t in await memory.get_tasks(uid)
             if t.get("due") and agenda.is_today(t["due"])]
    cal = await asyncio.to_thread(gcal.list_events, 0, 0, cfg.timezone)
    lines = [f"🗓 *Сегодня ({_WD_NAMES[wd]})*"]
    if cal:
        lines.append("\n📅 *Календарь:*")
        lines += [f"• {(e['time']+' ' if e['time'] else '')}{e['summary']}"
                  + (f" — {e['location']}" if e['location'] else "") for e in cal]
    if classes:
        lines.append("\n📚 *Пары:*")
        lines += [f"• {_fmt_class(c)}" for c in classes]
    if tasks:
        lines.append("\n✅ *Задачи:*")
        lines += [f"• {t['title']}" for t in tasks]
    if not cal and not classes and not tasks:
        lines.append("\nПусто — отдыхай 🙂")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    sched = await memory.list_schedule(update.effective_user.id)
    if not sched:
        await update.effective_message.reply_text(
            "📚 Расписание пустое.\nПродиктуй обычным сообщением, например:\n"
            "«по понедельникам в 9 матан в 305, по средам в 11 физика» — я добавлю.",
        )
        return
    by_day: dict = {}
    for c in sched:
        by_day.setdefault(c["weekday"], []).append(c)
    lines = ["📚 *Расписание:*"]
    for wd in range(7):
        if wd in by_day:
            lines.append(f"\n*{_WD_NAMES[wd]}:*")
            lines += [f"• {_fmt_class(c)}" for c in by_day[wd]]
    lines.append("\nОчистить и продиктовать заново: /clearschedule")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_clearschedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await memory.clear_schedule(update.effective_user.id)
    await update.effective_message.reply_text("📚 Расписание очищено. Продиктуй новое одним сообщением.")


async def cmd_projects(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    projects = await memory.list_projects(update.effective_user.id)
    if not projects:
        await update.effective_message.reply_text(
            "💻 Проектов пока нет.\nПросто расскажи о работе — «по BTS задеплоил лендинг», "
            "«начал проект X» — я запомню статус."
        )
        return
    lines = ["💻 *Твои проекты:*"]
    for p in projects:
        lines.append(f"• *{p['name']}*" + (f" — {p['status']}" if p.get("status") else ""))
    lines.append("\nУдалить: `/delproject <название>`")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_delproject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_message.reply_text("Формат: `/delproject <название>`", parse_mode="Markdown")
        return
    ok = await memory.delete_project(update.effective_user.id, parts[1])
    await update.effective_message.reply_text(
        f"🗑 Удалён проект: {parts[1]}" if ok else f"Не найден проект: {parts[1]}"
    )


async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    arg = " ".join(ctx.args).strip()
    if not arg.isdigit():
        await update.effective_message.reply_text(
            "Укажи номер задачи из `/tasks`. Пример: `/done 2`", parse_mode="Markdown"
        )
        return
    uid = update.effective_user.id
    tasks = await memory.get_tasks(uid)
    n = int(arg)
    if n < 1 or n > len(tasks):
        await update.effective_message.reply_text(
            f"Нет задачи №{n}. Посмотри список: /tasks"
        )
        return
    target = tasks[n - 1]
    await memory.complete_task(uid, target["id"])
    await update.effective_message.reply_text(f"Закрыто ✅ «{target['title']}»")


async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    arg = " ".join(ctx.args).strip()
    if not arg:
        current = await memory.get_mode(uid) or personas.DEFAULT_MODE
        await update.effective_message.reply_text(
            personas.list_text(current), parse_mode="Markdown",
            reply_markup=_mode_keyboard()
        )
        return
    mid = personas.resolve(arg)
    if not mid:
        await update.effective_message.reply_text(
            "Не знаю такой режим 🤔\n\n" + personas.list_text(await memory.get_mode(uid)),
            parse_mode="Markdown",
        )
        return
    await memory.set_mode(uid, mid)
    await update.effective_message.reply_text(
        f"Готово, переключился в режим: {personas.label(mid)} ✅"
    )


async def cmd_forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    await memory.clear(uid)
    gemini.clear_history(uid)
    await update.effective_message.reply_text(
        "Стёр всё, что о тебе знал. Начинаем с чистого листа 🧹"
    )


# ── PC commands ────────────────────────────────────────────────────────────────

async def cmd_contacts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    contacts = await memory.list_contacts(uid)
    if not contacts:
        await update.effective_message.reply_text(
            "📇 Белый список пуст.\n\n"
            "Добавь, кому JARVIS может писать:\n"
            "`/addcontact <имя> <@username или +телефон>`\n"
            "Пример: `/addcontact айгуль @aigul_k`",
            parse_mode="Markdown",
        )
        return
    lines = "\n".join(f"• *{c['alias']}* → `{c['target']}`" for c in contacts)
    await update.effective_message.reply_text(
        f"📇 *Кому можно писать:*\n{lines}\n\n"
        f"Сказать: «отправь {contacts[0]['alias']} голосом, что опоздаю»\n"
        f"Удалить: `/delcontact {contacts[0]['alias']}`",
        parse_mode="Markdown",
    )


async def cmd_addcontact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    parts = (update.effective_message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await update.effective_message.reply_text(
            "Формат: `/addcontact <имя> <@username или +телефон> [кто это]`\n"
            "Примеры:\n"
            "`/addcontact айгуль @aigul_k`\n"
            "`/addcontact брат @sanjar младший брат, неформально`\n"
            "(заметка «кто это» помогает JARVIS подобрать тон)",
            parse_mode="Markdown",
        )
        return
    alias, target = parts[1], parts[2].strip()
    note = parts[3].strip() if len(parts) > 3 else ""
    await memory.add_contact(uid, alias, target, note)
    msg = f"✅ Добавлен: *{alias.lower()}* → `{target}`"
    if note:
        msg += f"\n👤 кто это: {note}"
    msg += f"\nТеперь можно: «отправь {alias.lower()} голосом, …»"
    await update.effective_message.reply_text(msg, parse_mode="Markdown")


async def cmd_delcontact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_message.reply_text("Формат: `/delcontact <имя>`", parse_mode="Markdown")
        return
    ok = await memory.del_contact(uid, parts[1])
    await update.effective_message.reply_text(
        f"🗑 Удалён: {parts[1].lower()}" if ok else f"Не найден: {parts[1]}"
    )


async def cmd_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    notes = await memory.list_notes(uid)
    if not notes:
        await update.effective_message.reply_text(
            "📝 Заметок пока нет.\nКинь мысль — «запиши: …» или просто скажи идею, "
            "я сам разложу её по полкам."
        )
        return
    lines = "\n".join(f"{i}. {n['text']}" for i, n in enumerate(notes, 1))
    await update.effective_message.reply_text(
        f"📝 *Твои заметки:*\n{lines}\n\nУдалить: `/delnote <номер>`", parse_mode="Markdown"
    )


async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_message.reply_text("Формат: `/note <текст>`", parse_mode="Markdown")
        return
    await memory.add_note(update.effective_user.id, parts[1])
    await update.effective_message.reply_text("📝 Записал в заметки.")


async def cmd_delnote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    parts = (update.effective_message.text or "").split(maxsplit=1)
    notes = await memory.list_notes(uid)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await update.effective_message.reply_text("Формат: `/delnote <номер из /notes>`", parse_mode="Markdown")
        return
    idx = int(parts[1].strip())
    if idx < 1 or idx > len(notes):
        await update.effective_message.reply_text("Нет заметки с таким номером.")
        return
    await memory.delete_note(uid, notes[idx - 1]["id"])
    await update.effective_message.reply_text(f"🗑 Удалил заметку #{idx}.")


async def cmd_findnote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    parts = (update.effective_message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_message.reply_text("Формат: `/findnote <слово>`", parse_mode="Markdown")
        return
    q = parts[1].strip()
    rows = await memory.search_notes(update.effective_user.id, q)
    if not rows:
        await update.effective_message.reply_text(f"🔍 По «{q}» в заметках ничего не нашёл.")
        return
    lines = [f"🔍 Нашёл по «{q}»:"] + [f"• {r['text']}" for r in rows]
    await update.effective_message.reply_text("\n".join(lines))


_PC_OFFLINE = (
    "❌ ПК офлайн.\n"
    "Запусти на компьютере `scripts\\start_pc.bat` "
    "(или `python -m telegram_bot.pc_server`)."
)


async def _run_pc(message, command: str, user_id: int, *,
                  timeout: float = 25.0, action: str = "typing") -> None:
    """Единственный путь «послать команду на ПК и ответить пользователю».

    Раньше это делали шесть обработчиков, каждый по-своему: одни звали
    send_command (только текст) и теряли снимок, другие send_command_full;
    сообщение «ПК офлайн» жило в десяти формулировках. Отсюда и то, что
    «сделай скриншот» присылал «✅» без картинки, а /pc — до сих пор.
    """
    if not bridge.connected:
        await message.reply_text(_PC_OFFLINE, parse_mode="Markdown")
        return
    try:
        await message.chat.send_action(action)
    except Exception as exc:
        logger.debug("Индикатор набора не показался: %s", exc)
    result = await bridge.send_command_full(command, user_id, timeout=timeout)
    await _reply_pc_result(message, result)


async def cmd_pc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    command = " ".join(ctx.args).strip()
    if not command:
        await update.effective_message.reply_text(
            "Использование: /pc <команда>\nПример: /pc поставь believer"
        )
        return
    await _run_pc(update.effective_message, command, update.effective_user.id)


async def cmd_screenshot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await _run_pc(update.effective_message, "скриншот", update.effective_user.id,
                  action="upload_photo")


async def cmd_camera(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await _run_pc(update.effective_message, "снимок с камеры", update.effective_user.id,
                  timeout=30.0, action="upload_photo")


async def cmd_vol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    val = " ".join(ctx.args).strip()
    if not val.isdigit() or not (0 <= int(val) <= 100):
        await update.effective_message.reply_text(
            "Использование: /vol [0–100]\nПример: /vol 70"
        )
        return
    await _run_pc(update.effective_message, f"системная громкость {val}",
                  update.effective_user.id)


async def cmd_lock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await _run_pc(update.effective_message, "заблокируй экран", update.effective_user.id)


async def cmd_sysinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await _run_pc(update.effective_message, "системная информация", update.effective_user.id)


async def cmd_briefing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await _run_pc(update.effective_message, "брифинг", update.effective_user.id)


# ── Reminders ──────────────────────────────────────────────────────────────────

async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = " ".join(ctx.args).strip()
    if not text:
        await update.effective_message.reply_text(
            "Использование: /remind <когда> <что>\n\n"
            "Примеры:\n"
            "• /remind через 30 минут позвонить маме\n"
            "• /remind завтра в 9:00 встреча\n"
            "• /remind в 15:00 купить продукты"
        )
        return
    confirm = await _store_reminder(update.effective_user.id, text)
    if not confirm:
        await update.effective_message.reply_text(
            "Не понял когда напомнить. Попробуй:\n"
            "• через 30 минут\n"
            "• в 15:00\n"
            "• завтра в 9:00"
        )
        return
    await update.effective_message.reply_text(confirm)


# Gemini emits hidden directive blocks ([[REMINDERS]]/[[HABITS]]/[[TASKS]]) to
# actually create things from a free chat. directives.apply executes them.
async def _apply_reminder_directives(uid: int, reply: str) -> tuple[str, int]:
    tz = user_context.local_now(uid, cfg.timezone).tzinfo
    clean, summary = await directives.apply(memory, uid, reply, tz)
    return clean, summary


async def _store_reminder(uid: int, text: str) -> str | None:
    """Parse a reminder in the user's local time, save it durably (UTC), and
    return a confirmation string — or None if the phrase isn't parseable."""
    now_local = user_context.local_now(uid, cfg.timezone)
    parsed = parse_reminder(text, now_local)
    if not parsed:
        return None
    when, what = parsed
    await memory.add_reminder(uid, what, rem.to_utc_iso(when))
    return f"✅ Напомню: «{what}» — {rem.confirm_label(when, now_local)}"


async def cmd_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    uid = update.effective_user.id
    items = await memory.list_reminders(uid)
    if not items:
        await update.effective_message.reply_text("У тебя нет активных напоминаний.")
        return
    tz = user_context.local_now(uid, cfg.timezone).tzinfo
    lines = ["📋 *Твои напоминания:*"]
    for r in items:
        lines.append(f"  • {rem.fmt_local(r['due'], tz)} — {r['text']}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Message handlers ───────────────────────────────────────────────────────────

@contextlib.asynccontextmanager
async def _busy(chat, action: str = "typing"):
    """Keep Telegram's status indicator alive (it expires after ~5s) so long
    operations like voice synthesis don't look like the bot fell asleep."""
    async def _loop():
        with contextlib.suppress(Exception, asyncio.CancelledError):
            while True:
                await chat.send_action(action)
                await asyncio.sleep(4)
    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ── Document & link ingestion (summarize → memory) ──────────────────────────
_RE_URL = re.compile(r"https?://[^\s]+")


async def _fetch_url(url: str) -> str:
    """Fetch a page and crudely strip it to text (no extra deps)."""
    def _get():
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (JARVIS)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(2_000_000).decode("utf-8", errors="ignore")
        html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
        txt = re.sub(r"(?s)<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", txt).strip()[:20000]
    try:
        return await asyncio.to_thread(_get)
    except Exception as e:
        logger.debug(f"fetch_url: {e}")
        return ""


async def _handle_link(update: Update, user_id: int, url: str):
    msg = update.effective_message
    await msg.reply_text("🔗 Открываю и делаю конспект…")
    text = await _fetch_url(url)
    if not text or len(text) < 80:
        await msg.reply_text("❌ Не смог прочитать страницу (закрыта/пустая).")
        return
    summary = await gemini.summarize_source(user_id, text, url)
    await msg.reply_text(f"🔗 Конспект:\n\n{summary}")
    note = f"[Ссылка {url}] {summary[:600]}"
    await memory.add_note(user_id, note)
    asyncio.create_task(memory_rag.index(memory, gemini, user_id, "link", note))


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    msg = update.effective_message
    doc = msg.document
    user_id = update.effective_user.id
    await msg.chat.send_action("typing")
    try:
        if doc.file_size and doc.file_size > 15 * 1024 * 1024:
            await msg.reply_text("📄 Файл большой (>15 МБ) — пришли поменьше.")
            return
        f = await ctx.bot.get_file(doc.file_id)
        data = bytes(await f.download_as_bytearray())
        summary = await gemini.summarize_document(
            data, doc.mime_type or "", doc.file_name or "файл", msg.caption or "")
        if not summary or summary.startswith("Извини"):
            await msg.reply_text(summary or "❌ Не смог разобрать документ.")
            return
        await msg.reply_text(f"📄 *{doc.file_name or 'документ'}*\n\n{summary}",
                             parse_mode="Markdown")
        note = f"[Документ {doc.file_name or ''}] {summary[:600]}"
        await memory.add_note(user_id, note)
        asyncio.create_task(memory_rag.index(memory, gemini, user_id, "document", note))
    except Exception as e:
        logger.error(f"handle_document error: {e}")
        await msg.reply_text("❌ Не смог обработать документ.")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = update.effective_message.text
    user_id = update.effective_user.id
    await update.effective_message.chat.send_action("typing")

    try:
        # 0. Onboarding in progress? Capture the answer first.
        if onboarding.is_active(user_id):
            await memory.ensure_loaded(user_id)
            reply = await onboarding.handle(memory, user_id, text)
            await update.effective_message.reply_text(reply, parse_mode="Markdown")
            return

        # 0.5 If JARVIS asked a get-to-know-you question, save this as the answer
        #     (still falls through so JARVIS also replies naturally).
        await memory.ensure_loaded(user_id)
        await curiosity.save_answer(memory, user_id, text)

        # 0.6 A bare link? Fetch + summarize + remember it.
        _u = _RE_URL.search(text or "")
        if _u and len((text or "").strip()) < 300 and (text or "").strip().startswith("http"):
            await _handle_link(update, user_id, _u.group(0))
            return

        # 1. Clean single reminder ("напомни …")? Fast-path it. If it doesn't
        #    parse, fall through to Gemini's unified inbox instead of a dead-end
        #    prompt — the inbox handles complex brain-dumps (reminder+task+note).
        if _looks_like_reminder(text):
            confirm = await _store_reminder(user_id, text)
            if confirm:
                await update.effective_message.reply_text(confirm)
                return
            # else: not a parseable single reminder → let the inbox sort it

        # 2. PC command?
        if _looks_like_pc_command(text):
            await _run_pc(update.effective_message, text, user_id)
            return

        # 3. Gemini conversation (with long-term memory). JARVIS may decide to
        #    create reminders/tasks/habits or send a message to a contact —
        #    handled via hidden [[...]] directive blocks it composes itself.
        await memory.ensure_loaded(user_id)
        # Re-seed the chat window from the durable log after a restart so the
        # conversation stays continuous (the RAM window is otherwise lost).
        if not gemini.has_history(user_id):
            gemini.seed_history(user_id, await memory.recent_messages(user_id, 40))
        reply = await gemini.chat(user_id, text)
        reply = await _resolve_fetch(user_id, reply)          # bounded 1-step action chain
        reply, summary = await _apply_reminder_directives(user_id, reply)
        reply = await _apply_send_directives(update, user_id, reply)
        if summary:
            reply += "\n\n✅ Добавил — " + ", ".join(summary)
        if reply.strip():
            # Голосом на текстовую просьбу. Раньше голос был только в ответ на
            # голосовое, поэтому на «отвечай мне голосом» модель отвечала, что
            # не умеет, — хотя синтез рядом и работает.
            if _wants_voice_reply(text):
                ogg = await voice.speak_ogg(reply, gemini)
                if ogg:
                    cap = reply if len(reply) <= 1000 else reply[:997] + "…"
                    await update.effective_message.reply_voice(voice=ogg, caption=cap)
                else:
                    await update.effective_message.reply_text(reply)
            else:
                await update.effective_message.reply_text(reply)
        # Background, never blocks the reply: learn durable facts + log the raw
        # exchange forever (foundation for full recall).
        asyncio.create_task(memory.observe(user_id, gemini, text, reply))
        asyncio.create_task(_persist_exchange(user_id, text, reply))
    except Exception as e:
        logger.error(f"handle_text error: {e}")
        await update.effective_message.reply_text("❌ Что-то пошло не так. Попробуй ещё раз.")


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    user_id = update.effective_user.id
    msg = update.effective_message
    try:
        # Show "recording audio…" the whole time — voice synthesis takes a while
        # and a one-shot indicator expires in 5s, making the bot look asleep.
        async with _busy(msg.chat, "record_voice"):
            file = await ctx.bot.get_file(msg.voice.file_id)
            audio = bytes(await file.download_as_bytearray())

            # Transcribe, then route through the same pipeline as text — so voice
            # commands control the PC, not just chat.
            transcript = await gemini.transcribe(audio, mime_type="audio/ogg")

            # 0. Onboarding in progress? Capture the spoken answer too (not just
            # text), otherwise a voice reply would silently skip the question.
            if onboarding.is_active(user_id):
                await memory.ensure_loaded(user_id)
                if not transcript:
                    await msg.reply_text("Не расслышал, повтори голосом или напиши текстом 🙏")
                    return
                reply = await onboarding.handle(memory, user_id, transcript)
                ogg = await voice.speak_ogg(reply, gemini)
                if ogg:
                    cap = reply if len(reply) <= 1000 else reply[:997] + "…"
                    await msg.reply_voice(voice=ogg, caption=cap, parse_mode="Markdown")
                else:
                    await msg.reply_text(reply, parse_mode="Markdown")
                return

            if transcript and _looks_like_pc_command(transcript):
                await msg.reply_text(f"🎙 «{transcript}»")
                await _run_pc(msg, transcript, user_id)
                return

            # Расшифровка не удалась из-за лимита — говорим правду. Иначе
            # пустой текст уходил в модель, и та сочиняла «голосовое пришло
            # пустым или не записалось», сваливая вину на микрофон владельца.
            if not transcript and getattr(gemini, "last_error", "") == "quota":
                await msg.reply_text(
                    "🎙 Не могу распознать голос: исчерпан бесплатный лимит Gemini "
                    "на распознавание речи. Напиши текстом — на текст лимит "
                    "отдельный, — или попробуй голосом позже."
                )
                return

            # Otherwise — normal voice conversation. Voice in → voice out (mirror).
            # JARVIS may also send to a contact via a [[SEND]] block it composes.
            await memory.ensure_loaded(user_id)
            if not gemini.has_history(user_id):
                gemini.seed_history(user_id, await memory.recent_messages(user_id, 40))
            reply = await gemini.chat_with_audio(user_id, audio, recall_text=transcript or "")
            reply, summary = await _apply_reminder_directives(user_id, reply)
            reply = await _apply_send_directives(update, user_id, reply)
            if summary:
                reply += "\n\n✅ Добавил — " + ", ".join(summary)
            if not reply.strip():
                return                      # only a send directive — preview already shown
            ogg = await voice.speak_ogg(reply, gemini)
            if ogg:
                caption = reply if len(reply) <= 1000 else reply[:997] + "…"
                await msg.reply_voice(voice=ogg, caption=caption)
            else:
                await msg.reply_text(reply)
            if transcript:
                asyncio.create_task(memory.observe(user_id, gemini, transcript, reply))
                asyncio.create_task(_persist_exchange(user_id, transcript, reply))
    except Exception as e:
        logger.error(f"handle_voice error: {e}")
        await msg.reply_text("❌ Не смог обработать голосовое.")


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.effective_message.chat.send_action("typing")
    try:
        photo = update.effective_message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        image = bytes(await file.download_as_bytearray())
        caption = update.effective_message.caption or ""
        reply = await gemini.chat_with_image(update.effective_user.id, image, caption)
        await update.effective_message.reply_text(reply)
    except Exception as e:
        logger.error(f"handle_photo error: {e}")
        await update.effective_message.reply_text("❌ Не смог обработать фото.")


# ── PC notifications ───────────────────────────────────────────────────────────

async def _on_notification(text: str, user_id: int = None, bot=None):
    if bot is None:
        return
    targets = [user_id] if user_id else cfg.allowed_user_ids
    for uid in targets:
        try:
            await bot.send_message(chat_id=uid, text=f"🔔 {text}")
        except Exception as e:
            logger.error(f"Notify {uid}: {e}")


# ── Reminder loop ──────────────────────────────────────────────────────────────

async def _reminder_loop(bot):
    while True:
        try:
            await asyncio.sleep(30)
            for r in await memory.get_due_reminders(rem.now_utc_iso()):
                try:
                    await bot.send_message(
                        chat_id=r["user_id"],
                        text=f"🔔 Напоминание: {r['text']}"
                    )
                    await memory.mark_reminder_sent(r["id"])
                except Exception as e:
                    logger.error(f"Reminder send: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Reminder loop: {e}")


# ── Startup ────────────────────────────────────────────────────────────────────

_bridge_task: asyncio.Task | None = None
_reminder_task: asyncio.Task | None = None
_miniapp_task: asyncio.Task | None = None
_proactive_task: asyncio.Task | None = None
_memory_task: asyncio.Task | None = None


def main():
    async def post_init(application: Application) -> None:
        global _bridge_task, _reminder_task, _miniapp_task, _proactive_task
        global _memory_task
        await application.bot.set_my_commands(_BOT_COMMANDS)
        # init() never raises — a dead DB degrades memory, it must not stop the bot
        await memory.init()
        bridge.on_notification(
            lambda t, uid: _on_notification(t, uid, application.bot)
        )
        loop = asyncio.get_event_loop()
        _memory_task    = loop.create_task(memory.watch())
        _bridge_task    = loop.create_task(bridge.connect_loop())
        _reminder_task  = loop.create_task(_reminder_loop(application.bot))
        _proactive_task = loop.create_task(
            proactive.loop(application.bot, gemini, memory, cfg.timezone, cfg.default_city)
        )

        # Start Mini App server if port is configured
        if cfg.miniapp_port:
            try:
                from telegram_bot import miniapp_server
                miniapp_server._memory = memory
                from telegram_bot.miniapp_server import run as run_miniapp
                _miniapp_task = loop.create_task(
                    run_miniapp(port=cfg.miniapp_port, gemini=gemini, bridge=bridge)
                )
                logger.info(f"Mini App server started on port {cfg.miniapp_port}")
            except Exception as e:
                logger.warning(f"Mini App server not started: {e}")

        logger.info("JARVIS Bot initialized ✅")

    async def post_shutdown(application: Application) -> None:
        for task in (_bridge_task, _reminder_task, _miniapp_task, _proactive_task,
                     _memory_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = (
        ApplicationBuilder()
        .token(cfg.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("app",        cmd_app))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("mode",       cmd_mode))
    app.add_handler(CommandHandler("profile",    cmd_profile))
    app.add_handler(CommandHandler("memstats",   cmd_memstats))
    app.add_handler(CommandHandler("journal",    cmd_journal))
    app.add_handler(CommandHandler("reindex",    cmd_reindex))
    app.add_handler(CommandHandler("ask",        cmd_ask))
    app.add_handler(CommandHandler("curiosity",  cmd_curiosity))
    app.add_handler(CommandHandler("remember",   cmd_remember))
    app.add_handler(CommandHandler("forget",     cmd_forget))
    app.add_handler(CommandHandler("pc",         cmd_pc))
    app.add_handler(CommandHandler("screenshot", cmd_screenshot))
    app.add_handler(CommandHandler("camera",     cmd_camera))
    app.add_handler(CommandHandler("vol",        cmd_vol))
    app.add_handler(CommandHandler("lock",       cmd_lock))
    app.add_handler(CommandHandler("sysinfo",    cmd_sysinfo))
    app.add_handler(CommandHandler("briefing",   cmd_briefing))
    app.add_handler(CommandHandler("remind",     cmd_remind))
    app.add_handler(CommandHandler("reminders",  cmd_reminders))
    app.add_handler(CommandHandler("task",       cmd_task))
    app.add_handler(CommandHandler("tasks",      cmd_tasks))
    app.add_handler(CommandHandler("today",      cmd_today))
    app.add_handler(CommandHandler("done",       cmd_done))
    app.add_handler(CommandHandler("habit",      cmd_habit))
    app.add_handler(CommandHandler("habits",     cmd_habits))
    app.add_handler(CommandHandler("check",      cmd_check))
    app.add_handler(CommandHandler("morning",    cmd_morning))
    app.add_handler(CommandHandler("evening",    cmd_evening))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    logger.info("Starting JARVIS Telegram Bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
