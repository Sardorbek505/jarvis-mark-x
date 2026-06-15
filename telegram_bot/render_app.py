"""
Render entry point — Telegram bot (webhook) + Mini App server in one process.

Webhook mode eliminates telegram.error.Conflict — no more polling fights between
deploys. Telegram POSTs updates to /telegram-webhook with a secret header;
FastAPI feeds them into the Application's update queue.
"""
import asyncio
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Update

from telegram_bot.config import load as load_config
from telegram_bot.gemini_client import GeminiClient
from telegram_bot.pc_bridge import PCBridge
from telegram_bot import miniapp_server
from telegram_bot import user_context
from telegram_bot import personas
from telegram_bot import proactive
from telegram_bot.memory_store import MemoryStore

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("jarvis-render")

cfg = load_config()

# Webhook secret (Telegram sends it back in the X-Telegram-Bot-Api-Secret-Token
# header). Derived from the bot token but stripped to the allowed charset
# [A-Za-z0-9_-] — the raw token contains ':' which is NOT allowed here and
# also breaks when placed in a URL path. Static path + header = robust delivery.
_WEBHOOK_SECRET = re.sub(r"[^A-Za-z0-9_-]", "", cfg.telegram_token)[:256]
_WEBHOOK_PATH = "/telegram-webhook"

# Shared instances
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

# Wire into miniapp_server
miniapp_server._gemini = gemini
miniapp_server._bridge = bridge
miniapp_server._memory = memory
bridge.on_status_change(miniapp_server.broadcast_pc_status)


# ── Build the Telegram Application (webhook mode, no Updater) ─────────────────

_tg_app = None  # set during lifespan startup


async def _build_tg_app():
    from telegram.ext import (
        ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters,
    )

    import telegram_bot.bot as botmod
    botmod.gemini = gemini
    botmod.bridge = bridge
    botmod.memory = memory

    from telegram_bot.bot import (
        cmd_start, cmd_help, cmd_app, cmd_status, cmd_clear,
        cmd_pc, cmd_screenshot, cmd_camera, cmd_vol, cmd_lock, cmd_sysinfo, cmd_briefing,
        cmd_remind, cmd_reminders, cmd_task, cmd_tasks, cmd_today, cmd_done,
        cmd_habit, cmd_habits, cmd_check,
        cmd_morning, cmd_evening, cmd_mode, cmd_profile, cmd_remember, cmd_forget,
        on_callback,
        handle_text, handle_voice, handle_photo,
        _on_notification, _BOT_COMMANDS,
    )

    app = (
        ApplicationBuilder()
        .token(cfg.telegram_token)
        .updater(None)          # disable polling/updater — we receive via webhook
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
    app.add_handler(CommandHandler("habit",      cmd_habit))
    app.add_handler(CommandHandler("habits",     cmd_habits))
    app.add_handler(CommandHandler("check",      cmd_check))
    app.add_handler(CommandHandler("morning",    cmd_morning))
    app.add_handler(CommandHandler("evening",    cmd_evening))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    bridge.on_notification(lambda t, uid: _on_notification(t, uid, app.bot))

    return app, _BOT_COMMANDS


# ── Reminder loop ─────────────────────────────────────────────────────────────

_tasks: list[asyncio.Task] = []


async def _reminder_loop(bot):
    from telegram_bot.reminders import now_utc_iso
    while True:
        try:
            await asyncio.sleep(30)
            for r in await memory.get_due_reminders(now_utc_iso()):
                try:
                    await bot.send_message(chat_id=r["user_id"],
                                           text=f"🔔 Напоминание: {r['text']}")
                    await memory.mark_reminder_sent(r["id"])
                except Exception as e:
                    logger.error(f"Reminder send: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Reminder loop: {e}")


async def _set_webhook(bot, webhook_url: str, drop_pending: bool = False):
    await bot.set_webhook(
        url=webhook_url,
        secret_token=_WEBHOOK_SECRET,
        drop_pending_updates=drop_pending,
        allowed_updates=["message", "edited_message", "callback_query"],
    )


async def _webhook_keeper(bot, webhook_url: str):
    """Re-assert the webhook if anything wipes it (e.g. a stray local bot
    running getUpdates deletes it). Also surfaces Telegram delivery errors so
    they show up in the Render logs. Keeps the bot responsive automatically."""
    while True:
        try:
            await asyncio.sleep(90)
            info = await bot.get_webhook_info()
            if info.last_error_message:
                logger.warning(
                    f"Telegram webhook last error: {info.last_error_message} "
                    f"(pending={info.pending_update_count})"
                )
            if info.url != webhook_url:
                await _set_webhook(bot, webhook_url)
                logger.warning(f"Webhook was lost (was '{info.url}') — re-registered ✅")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Webhook keeper: {e}")


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_app
    logger.info("Render app starting…")

    await memory.init()

    _tg_app, bot_commands = await _build_tg_app()
    await _tg_app.initialize()
    await _tg_app.start()
    await _tg_app.bot.set_my_commands(bot_commands)

    if cfg.miniapp_url:
        webhook_url = f"{cfg.miniapp_url.rstrip('/')}{_WEBHOOK_PATH}"
        await _set_webhook(_tg_app.bot, webhook_url, drop_pending=True)
        logger.info(f"Webhook registered: {webhook_url}")
        _tasks.append(asyncio.create_task(_webhook_keeper(_tg_app.bot, webhook_url)))
    else:
        logger.warning(
            "MINIAPP_URL not set — webhook NOT registered. "
            "Set MINIAPP_URL in Render env to https://<your-app>.onrender.com"
        )

    _tasks.append(asyncio.create_task(_reminder_loop(_tg_app.bot)))
    _tasks.append(asyncio.create_task(
        proactive.loop(_tg_app.bot, gemini, memory, cfg.timezone, cfg.default_city)
    ))
    logger.info("JARVIS started ✅ (webhook mode)")

    yield

    for t in _tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    if cfg.miniapp_url:
        try:
            await _tg_app.bot.delete_webhook()
        except Exception:
            pass
    await _tg_app.stop()
    await _tg_app.shutdown()
    logger.info("Render app stopped.")


# ── Attach lifespan and routes to miniapp_server's FastAPI app ────────────────

miniapp_server.app.router.lifespan_context = lifespan


@miniapp_server.app.post(_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Telegram calls this endpoint for every update."""
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != _WEBHOOK_SECRET:
        logger.warning("Webhook rejected — bad secret token")
        return JSONResponse({"ok": False}, status_code=403)
    if _tg_app is None:
        return JSONResponse({"ok": False}, status_code=503)
    try:
        data = await request.json()
        update = Update.de_json(data, _tg_app.bot)
        # Hand off to the running Application's queue → processed asynchronously
        # so Telegram gets a fast 200 OK and never times out on slow handlers.
        await _tg_app.update_queue.put(update)
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
    # Always 200 so Telegram doesn't spam retries on transient handler errors.
    return JSONResponse({"ok": True})


@miniapp_server.app.get("/health")
async def health():
    """Keep-alive endpoint — ping every 5 min to prevent Render sleep."""
    return JSONResponse({"status": "ok", "pc": bridge.connected})


# Export for uvicorn
app = miniapp_server.app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", cfg.miniapp_port or 8000))
    uvicorn.run("telegram_bot.render_app:app", host="0.0.0.0", port=port, log_level="info")
