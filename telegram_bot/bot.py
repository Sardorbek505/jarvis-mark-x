#!/usr/bin/env python3
"""JARVIS Telegram Bot — mobile interface, runs on VPS."""
import asyncio
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

cfg = load_config()
gemini = GeminiClient(cfg.gemini_api_key, cfg.gemini_model)
bridge = PCBridge(cfg.pc_ws_host, cfg.pc_ws_port)

_BOT_COMMANDS = [
    BotCommand("start",    "Запустить JARVIS"),
    BotCommand("app",      "Открыть с голосом (Mini App)"),
    BotCommand("status",   "Статус подключения к ПК"),
    BotCommand("pc",       "Отправить команду на ПК"),
    BotCommand("remind",   "Добавить напоминание"),
    BotCommand("reminders","Мои напоминания"),
    BotCommand("clear",    "Очистить историю диалога"),
    BotCommand("help",     "Список команд"),
]

_PC_KEYWORDS = [
    # Music
    "play", "stop", "pause", "next", "prev", "volume",
    "включи", "выключи", "стоп", "пауза", "следующий",
    "поставь", "запусти", "воспроизведи", "играй",
    # Apps
    "open", "открой",
    # Weather
    "weather", "погода",
    # Search
    "search", "найди", "поищи",
    # Window control
    "сверни", "свернуть", "minimize", "рабочий стол", "разверни",
    "закрой окно", "переключи окно", "проводник", "диспетчер",
    # Screenshot
    "screenshot", "скриншот",
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


# ── Команды ────────────────────────────────────────────────────────────────────

def _app_keyboard() -> InlineKeyboardMarkup | None:
    if not cfg.miniapp_url:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬡ Открыть JARVIS", web_app=WebAppInfo(url=cfg.miniapp_url))
    ]])


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    name = update.effective_user.first_name or "сэр"
    pc = "онлайн ✅" if bridge.connected else "офлайн ❌"
    await update.message.reply_text(
        f"Привет, {name}! Я JARVIS — твой личный ИИ-ассистент.\n\n"
        f"🖥 ПК: {pc}\n"
        f"🤖 Gemini: готов ✅\n\n"
        f"Напиши что угодно — поговорим, помогу, отвечу.\n"
        f"/pc <команда> — управление компьютером\n"
        f"/remind <текст> — напоминание\n"
        f"/help — все команды",
        reply_markup=_app_keyboard(),
    )


async def cmd_app(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    kb = _app_keyboard()
    if kb:
        await update.message.reply_text("Открываю JARVIS ↓", reply_markup=kb)
    else:
        await update.message.reply_text(
            "Mini App не настроен.\n"
            "Добавь miniapp_url в config/api_keys.json и перезапусти бота."
        )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    pc_status = "онлайн ✅" if bridge.connected else "офлайн ❌"
    await update.message.reply_text(
        f"📋 *Команды JARVIS*\n\n"
        f"*Компьютер* (ПК: {pc_status})\n"
        f"/pc сверни все окна\n"
        f"/pc включи музыку\n"
        f"/pc поставь believer\n"
        f"/pc погода в Ташкенте\n"
        f"/pc найди новости о AI\n\n"
        f"*Напоминания*\n"
        f"/remind через 30 минут позвонить маме\n"
        f"/remind завтра в 10:00 встреча\n"
        f"/reminders — список активных\n\n"
        f"*Прочее*\n"
        f"/status — статус ПК\n"
        f"/clear — очистить историю\n"
        f"/app — голосовой интерфейс\n\n"
        f"💬 Или просто пиши — я твой ассистент!",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    pc = "онлайн ✅" if bridge.connected else "офлайн ❌"
    await update.message.reply_text(f"🖥 ПК: {pc}\n🤖 Gemini: готов ✅")


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    gemini.clear_history(update.effective_user.id)
    await update.message.reply_text("История диалога очищена ✅")


async def cmd_pc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    command = " ".join(ctx.args).strip()
    if not command:
        await update.message.reply_text("Использование: /pc <команда>\nПример: /pc сверни все окна")
        return
    if not bridge.connected:
        await update.message.reply_text(
            "❌ ПК офлайн.\n"
            "Запусти на своём компьютере:\n"
            "`python -m telegram_bot.pc_server`",
            parse_mode="Markdown",
        )
        return
    await update.message.chat.send_action("typing")
    result = await bridge.send_command(command, update.effective_user.id)
    await update.message.reply_text(result or "❌ ПК не ответил вовремя.")


async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = " ".join(ctx.args).strip()
    if not text:
        await update.message.reply_text(
            "Использование: /remind <когда> <что>\n\n"
            "Примеры:\n"
            "• /remind через 30 минут позвонить маме\n"
            "• /remind завтра в 9:00 встреча\n"
            "• /remind в 15:00 купить продукты"
        )
        return
    parsed = parse_reminder(text)
    if not parsed:
        await update.message.reply_text(
            "Не понял когда напомнить. Попробуй:\n"
            "• через 30 минут\n"
            "• в 15:00\n"
            "• завтра в 9:00"
        )
        return
    when, what = parsed
    reply = add_reminder(update.effective_user.id, what, when)
    await update.message.reply_text(reply)


async def cmd_reminders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.reply_text(list_reminders(update.effective_user.id))


# ── Сообщения ──────────────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = update.message.text
    user_id = update.effective_user.id
    await update.message.chat.send_action("typing")

    try:
        # 1. Check for reminder request (handle before PC/Gemini routing)
        if _looks_like_reminder(text):
            parsed = parse_reminder(text)
            if parsed:
                when, what = parsed
                reply = add_reminder(user_id, what, when)
                await update.message.reply_text(reply)
                return

        # 2. Try PC bridge for hardware commands
        pc_result = await _try_pc(text, user_id)
        if pc_result:
            await update.message.reply_text(f"🖥 {pc_result}")
            return

        # 3. Fall through to Gemini for conversation
        reply = await gemini.chat(user_id, text)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"handle_text error: {e}")
        await update.message.reply_text("❌ Что-то пошло не так. Попробуй ещё раз.")


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.chat.send_action("typing")
    try:
        file = await ctx.bot.get_file(update.message.voice.file_id)
        audio = bytes(await file.download_as_bytearray())
        reply = await gemini.chat_with_audio(update.effective_user.id, audio)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"handle_voice error: {e}")
        await update.message.reply_text("❌ Не смог обработать голосовое. Попробуй ещё раз.")


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.chat.send_action("typing")
    try:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        image = bytes(await file.download_as_bytearray())
        caption = update.message.caption or ""
        reply = await gemini.chat_with_image(update.effective_user.id, image, caption)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"handle_photo error: {e}")
        await update.message.reply_text("❌ Не смог обработать фото. Попробуй ещё раз.")


# ── Уведомления от ПК ──────────────────────────────────────────────────────────

async def _on_notification(text: str, user_id: int = None, bot=None):
    if bot is None:
        return
    targets = [user_id] if user_id else cfg.allowed_user_ids
    for uid in targets:
        try:
            await bot.send_message(chat_id=uid, text=f"🔔 {text}")
        except Exception as e:
            logger.error(f"Notify {uid}: {e}")


# ── Планировщик напоминаний ────────────────────────────────────────────────────

async def _reminder_loop(bot):
    """Check and send due reminders every 30 seconds."""
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
                    logger.error(f"Reminder send error: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Reminder loop error: {e}")


# ── Запуск ─────────────────────────────────────────────────────────────────────

_bridge_task: asyncio.Task | None = None
_reminder_task: asyncio.Task | None = None


def main():
    async def post_init(application: Application) -> None:
        global _bridge_task, _reminder_task
        await application.bot.set_my_commands(_BOT_COMMANDS)
        bridge.on_notification(
            lambda t, uid: _on_notification(t, uid, application.bot)
        )
        loop = asyncio.get_event_loop()
        _bridge_task = loop.create_task(bridge.connect_loop())
        _reminder_task = loop.create_task(_reminder_loop(application.bot))
        logger.info("JARVIS Bot initialized ✅")

    async def post_shutdown(application: Application) -> None:
        for task in (_bridge_task, _reminder_task):
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

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("app",       cmd_app))
    app.add_handler(CommandHandler("status",    cmd_status))
    app.add_handler(CommandHandler("clear",     cmd_clear))
    app.add_handler(CommandHandler("pc",        cmd_pc))
    app.add_handler(CommandHandler("remind",    cmd_remind))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Starting JARVIS Telegram Bot...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
