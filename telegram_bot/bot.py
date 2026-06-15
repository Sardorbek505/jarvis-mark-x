#!/usr/bin/env python3
"""JARVIS Telegram Bot — mobile interface, runs on VPS."""
import asyncio
import base64
import logging
import sys
from datetime import datetime
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_bot.config import load as load_config
from telegram_bot.gemini_client import GeminiClient
from telegram_bot.pc_bridge import PCBridge
from telegram_bot.reminders import add_reminder, get_due, mark_sent, list_reminders, parse_reminder

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
from telegram_bot.memory_store import MemoryStore

cfg = load_config()
gemini = GeminiClient(cfg.gemini_api_key, cfg.gemini_model)
bridge = PCBridge()
memory = MemoryStore()


def _build_context(uid: int) -> str:
    parts = [user_context.describe(uid, cfg.default_city, cfg.timezone)]
    mem = memory.cached_block(uid)
    if mem:
        parts.append(mem)
    persona = personas.overlay(memory.cached_mode(uid))
    if persona:
        parts.append(persona)
    return "\n".join(parts)


gemini.set_context_provider(_build_context)

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
    BotCommand("today",      "Задачи на сегодня"),
    BotCommand("done",       "Закрыть задачу по номеру"),
    BotCommand("morning",    "Утренний брифинг сейчас"),
    BotCommand("evening",    "Вечерний разбор сейчас"),
    BotCommand("mode",       "Режим личности (ментор/друг/бизнес)"),
    BotCommand("profile",    "Что JARVIS обо мне знает"),
    BotCommand("remember",   "Запомнить факт обо мне"),
    BotCommand("forget",     "Стереть всё обо мне"),
    BotCommand("clear",      "Очистить историю диалога"),
]

_PC_KEYWORDS = [
    # Music
    "play", "stop", "pause", "next", "prev",
    "включи", "выключи", "стоп", "пауза", "следующий",
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
]

_REMINDER_TRIGGERS = ["напомни", "remind me", "поставь напоминание", "таймер на"]


def _is_authorized(update: Update) -> bool:
    if not cfg.allowed_user_ids:
        return True
    return update.effective_user.id in cfg.allowed_user_ids


def _looks_like_pc_command(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _PC_KEYWORDS)


def _looks_like_reminder(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _REMINDER_TRIGGERS)


async def _try_pc(text: str, user_id: int) -> str | None:
    if bridge.connected and _looks_like_pc_command(text):
        return await bridge.send_command(text, user_id)
    return None


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


async def cmd_morning(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.effective_message.chat.send_action("typing")
    await proactive._send_briefing(
        ctx.bot, gemini, memory, update.effective_user.id, "morning", cfg.timezone
    )


async def cmd_evening(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.effective_message.chat.send_action("typing")
    await proactive._send_briefing(
        ctx.bot, gemini, memory, update.effective_user.id, "evening", cfg.timezone
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
        agenda.render_list(tasks), parse_mode="Markdown"
    )


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    tasks = [t for t in await memory.get_tasks(update.effective_user.id)
             if t.get("due") and agenda.is_today(t["due"])]
    await update.effective_message.reply_text(
        agenda.render_list(tasks, "🗓 *На сегодня*"), parse_mode="Markdown"
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
            personas.list_text(current), parse_mode="Markdown"
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

async def cmd_pc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    command = " ".join(ctx.args).strip()
    if not command:
        await update.effective_message.reply_text(
            "Использование: /pc <команда>\nПример: /pc поставь believer"
        )
        return
    if not bridge.connected:
        await update.effective_message.reply_text(
            "❌ ПК офлайн.\n"
            "Запусти на своём компьютере:\n"
            "`python -m telegram_bot.pc_server`\n"
            "или дважды кликни `scripts\\start_pc.bat`",
            parse_mode="Markdown",
        )
        return
    await update.effective_message.chat.send_action("typing")
    result = await bridge.send_command(command, update.effective_user.id)
    await update.effective_message.reply_text(result or "❌ ПК не ответил вовремя.")


async def cmd_screenshot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not bridge.connected:
        await update.effective_message.reply_text("❌ ПК офлайн.")
        return
    await update.effective_message.chat.send_action("upload_photo")
    result = await bridge.send_command_full("скриншот", update.effective_user.id)
    if not result:
        await update.effective_message.reply_text("❌ ПК не ответил вовремя.")
        return
    if result.get("image_b64"):
        photo = base64.b64decode(result["image_b64"])
        await update.effective_message.reply_photo(
            photo, caption=f"📸 {result.get('text', 'Скриншот')}"
        )
    else:
        await update.effective_message.reply_text(result.get("text") or "❌ Не удалось сделать скриншот")


async def cmd_camera(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not bridge.connected:
        await update.effective_message.reply_text("❌ ПК офлайн.")
        return
    await update.effective_message.chat.send_action("upload_photo")
    result = await bridge.send_command_full("снимок с камеры", update.effective_user.id, timeout=30.0)
    if not result:
        await update.effective_message.reply_text("❌ ПК не ответил вовремя.")
        return
    if result.get("image_b64"):
        photo = base64.b64decode(result["image_b64"])
        await update.effective_message.reply_photo(
            photo, caption=f"📷 {result.get('text', 'Снимок с камеры')}"
        )
    else:
        await update.effective_message.reply_text(result.get("text") or "❌ Не удалось сделать снимок с камеры")


async def cmd_vol(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    val = " ".join(ctx.args).strip()
    if not val.isdigit() or not (0 <= int(val) <= 100):
        await update.effective_message.reply_text(
            "Использование: /vol [0–100]\nПример: /vol 70"
        )
        return
    if not bridge.connected:
        await update.effective_message.reply_text("❌ ПК офлайн.")
        return
    result = await bridge.send_command(f"системная громкость {val}", update.effective_user.id)
    await update.effective_message.reply_text(result or "❌ ПК не ответил.")


async def cmd_lock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not bridge.connected:
        await update.effective_message.reply_text("❌ ПК офлайн.")
        return
    result = await bridge.send_command("заблокируй экран", update.effective_user.id)
    await update.effective_message.reply_text(result or "❌ ПК не ответил.")


async def cmd_sysinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not bridge.connected:
        await update.effective_message.reply_text("❌ ПК офлайн.")
        return
    await update.effective_message.chat.send_action("typing")
    result = await bridge.send_command("системная информация", update.effective_user.id)
    await update.effective_message.reply_text(
        result or "❌ ПК не ответил.", parse_mode="Markdown"
    )


async def cmd_briefing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    if not bridge.connected:
        await update.effective_message.reply_text(
            "❌ ПК офлайн. Запусти pc_server на компьютере."
        )
        return
    await update.effective_message.chat.send_action("typing")
    result = await bridge.send_command("брифинг", update.effective_user.id)
    await update.effective_message.reply_text(result or "❌ ПК не ответил.")


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
    parsed = parse_reminder(text)
    if not parsed:
        await update.effective_message.reply_text(
            "Не понял когда напомнить. Попробуй:\n"
            "• через 30 минут\n"
            "• в 15:00\n"
            "• завтра в 9:00"
        )
        return
    when, what = parsed
    await update.effective_message.reply_text(add_reminder(update.effective_user.id, what, when))


async def cmd_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.effective_message.reply_text(list_reminders(update.effective_user.id))


# ── Message handlers ───────────────────────────────────────────────────────────

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

        # 1. Reminder?
        if _looks_like_reminder(text):
            parsed = parse_reminder(text)
            if parsed:
                when, what = parsed
                await update.effective_message.reply_text(add_reminder(user_id, what, when))
                return

        # 2. PC command?
        if _looks_like_pc_command(text):
            if not bridge.connected:
                await update.effective_message.reply_text(
                    "❌ ПК офлайн.\n"
                    "Запусти `scripts\\start_pc.bat` на компьютере.",
                    parse_mode="Markdown",
                )
                return
            pc_result = await bridge.send_command(text, user_id)
            if pc_result is not None:
                await update.effective_message.reply_text(f"🖥 {pc_result}")
            else:
                await update.effective_message.reply_text(
                    "❌ ПК не ответил. Убедись что pc_server запущен и попробуй ещё раз."
                )
            return

        # 3. Gemini conversation (with long-term memory)
        await memory.ensure_loaded(user_id)
        reply = await gemini.chat(user_id, text)
        await update.effective_message.reply_text(reply)
        # Learn durable facts in the background — never blocks the reply
        asyncio.create_task(memory.observe(user_id, gemini, text, reply))
    except Exception as e:
        logger.error(f"handle_text error: {e}")
        await update.effective_message.reply_text("❌ Что-то пошло не так. Попробуй ещё раз.")


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    user_id = update.effective_user.id
    await update.effective_message.chat.send_action("typing")
    try:
        file = await ctx.bot.get_file(update.effective_message.voice.file_id)
        audio = bytes(await file.download_as_bytearray())

        # Transcribe, then route through the same pipeline as text — so voice
        # commands control the PC, not just chat.
        transcript = await gemini.transcribe(audio, mime_type="audio/ogg")
        if transcript and _looks_like_pc_command(transcript):
            await update.effective_message.reply_text(f"🎙 «{transcript}»")
            if not bridge.connected:
                await update.effective_message.reply_text(
                    "❌ ПК офлайн. Запусти `scripts\\start_pc.bat` на компьютере.",
                    parse_mode="Markdown",
                )
                return
            pc_result = await bridge.send_command(transcript, user_id)
            await update.effective_message.reply_text(
                f"🖥 {pc_result}" if pc_result is not None else "❌ ПК не ответил."
            )
            return

        # Otherwise — normal voice conversation. Voice in → voice out (mirror).
        await memory.ensure_loaded(user_id)
        reply = await gemini.chat_with_audio(user_id, audio)
        ogg = await gemini.speak_ogg(reply)
        if ogg:
            caption = reply if len(reply) <= 1000 else reply[:997] + "…"
            await update.effective_message.reply_voice(voice=ogg, caption=caption)
        else:
            await update.effective_message.reply_text(reply)
        if transcript:
            asyncio.create_task(memory.observe(user_id, gemini, transcript, reply))
    except Exception as e:
        logger.error(f"handle_voice error: {e}")
        await update.effective_message.reply_text("❌ Не смог обработать голосовое.")


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
            due = get_due(datetime.now())
            for r in due:
                try:
                    await bot.send_message(
                        chat_id=r["user_id"],
                        text=f"🔔 Напоминание: {r['text']}"
                    )
                    mark_sent(r["id"])
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


def main():
    async def post_init(application: Application) -> None:
        global _bridge_task, _reminder_task, _miniapp_task, _proactive_task
        await application.bot.set_my_commands(_BOT_COMMANDS)
        await memory.init()
        bridge.on_notification(
            lambda t, uid: _on_notification(t, uid, application.bot)
        )
        loop = asyncio.get_event_loop()
        _bridge_task    = loop.create_task(bridge.connect_loop())
        _reminder_task  = loop.create_task(_reminder_loop(application.bot))
        _proactive_task = loop.create_task(
            proactive.loop(application.bot, gemini, memory, cfg.timezone)
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
        for task in (_bridge_task, _reminder_task, _miniapp_task, _proactive_task):
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
    app.add_handler(CommandHandler("morning",    cmd_morning))
    app.add_handler(CommandHandler("evening",    cmd_evening))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Starting JARVIS Telegram Bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
