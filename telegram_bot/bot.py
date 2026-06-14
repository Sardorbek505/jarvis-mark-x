#!/usr/bin/env python3
"""JARVIS Telegram Bot — mobile interface, runs on VPS."""
import asyncio
import logging
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import (
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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("jarvis-bot")

cfg = load_config()
gemini = GeminiClient(cfg.gemini_api_key)
bridge = PCBridge(cfg.pc_ws_host, cfg.pc_ws_port)

# Keywords that look like PC control commands
_PC_KEYWORDS = [
    "play", "stop", "pause", "next", "prev", "volume",
    "включи", "выключи", "стоп", "пауза", "следующий",
    "open", "close", "открой", "закрой",
    "weather", "погода",
    "напомни", "reminder",
    "search", "найди", "поищи",
    "screenshot", "скриншот",
]


def _is_authorized(update: Update) -> bool:
    if not cfg.allowed_user_ids:
        return True
    return update.effective_user.id in cfg.allowed_user_ids


def _looks_like_pc_command(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _PC_KEYWORDS)


async def _try_pc(text: str, user_id: int) -> str | None:
    if bridge.connected and _looks_like_pc_command(text):
        return await bridge.send_command(text, user_id)
    return None


# ── Команды ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    pc = "онлайн ✅" if bridge.connected else "офлайн ❌"
    await update.message.reply_text(
        f"Привет! Я JARVIS — твой ИИ-ассистент в Telegram.\n\n"
        f"🖥 ПК: {pc}\n"
        f"🤖 Gemini: готов ✅\n\n"
        f"Напиши текст, отправь голосовое или фото.\n"
        f"/help — все команды"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "📋 *Команды*\n"
        "/status — статус подключения к ПК\n"
        "/pc `команда` — отправить команду напрямую на ПК\n"
        "/clear — очистить историю диалога\n\n"
        "Просто пиши — я понимаю текст, голос и фото.",
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
        await update.message.reply_text("Использование: /pc <команда>")
        return
    if not bridge.connected:
        await update.message.reply_text("❌ ПК офлайн. Запусти `python -m telegram_bot.pc_server` на компьютере.")
        return
    await update.message.chat.send_action("typing")
    result = await bridge.send_command(command, update.effective_user.id)
    await update.message.reply_text(result or "❌ ПК не ответил вовремя.")


# ── Сообщения ──────────────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    text = update.message.text
    user_id = update.effective_user.id
    await update.message.chat.send_action("typing")

    pc_result = await _try_pc(text, user_id)
    if pc_result:
        await update.message.reply_text(f"🖥 {pc_result}")
        return

    pc_status = "онлайн" if bridge.connected else "офлайн"
    reply = await gemini.chat(user_id, text, pc_status=pc_status)
    await update.message.reply_text(reply)


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.chat.send_action("typing")
    file = await ctx.bot.get_file(update.message.voice.file_id)
    audio = bytes(await file.download_as_bytearray())
    reply = await gemini.chat_with_audio(update.effective_user.id, audio)
    await update.message.reply_text(reply)


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        return
    await update.message.chat.send_action("typing")
    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    image = bytes(await file.download_as_bytearray())
    caption = update.message.caption or ""
    reply = await gemini.chat_with_image(update.effective_user.id, image, caption)
    await update.message.reply_text(reply)


# ── Запуск ─────────────────────────────────────────────────────────────────────

async def _on_notification(text: str, user_id: int = None, bot=None):
    """PC → Telegram push notification."""
    if bot is None:
        return
    targets = [user_id] if user_id else cfg.allowed_user_ids
    for uid in targets:
        try:
            await bot.send_message(chat_id=uid, text=f"🔔 {text}")
        except Exception as e:
            logger.error(f"Notify {uid}: {e}")


def main():
    app = ApplicationBuilder().token(cfg.telegram_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("pc", cmd_pc))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    bridge.on_notification(lambda t, uid: _on_notification(t, uid, app.bot))

    async def post_init(application):
        asyncio.create_task(bridge.connect_loop())

    app.post_init = post_init

    logger.info("JARVIS Telegram Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
