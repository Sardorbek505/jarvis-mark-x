"""
Render entry point — Telegram bot (webhook) + Mini App server in one process.

Webhook mode eliminates telegram.error.Conflict — no more polling fights between
deploys. Telegram POSTs updates to /webhook/<token>; FastAPI processes them.
"""
import asyncio
import logging
import os
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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("jarvis-render")

cfg = load_config()

# Shared instances
gemini = GeminiClient(cfg.gemini_api_key, cfg.gemini_model)
gemini.set_context_provider(
    lambda uid: user_context.describe(uid, cfg.default_city, cfg.timezone)
)
bridge = PCBridge()

# Wire into miniapp_server
miniapp_server._gemini = gemini
miniapp_server._bridge = bridge
bridge.on_status_change(miniapp_server.broadcast_pc_status)


# ── Build the Telegram Application (webhook mode, no Updater) ─────────────────

_tg_app = None  # set during lifespan startup


async def _build_tg_app():
    from telegram.ext import (
        ApplicationBuilder, CommandHandler, MessageHandler, filters,
    )

    import telegram_bot.bot as botmod
    botmod.gemini = gemini
    botmod.bridge = bridge

    from telegram_bot.bot import (
        cmd_start, cmd_help, cmd_app, cmd_status, cmd_clear,
        cmd_pc, cmd_screenshot, cmd_camera, cmd_vol, cmd_lock, cmd_sysinfo, cmd_briefing,
        cmd_remind, cmd_reminders,
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
    app.add_handler(CommandHandler("pc",         cmd_pc))
    app.add_handler(CommandHandler("screenshot", cmd_screenshot))
    app.add_handler(CommandHandler("camera",     cmd_camera))
    app.add_handler(CommandHandler("vol",        cmd_vol))
    app.add_handler(CommandHandler("lock",       cmd_lock))
    app.add_handler(CommandHandler("sysinfo",    cmd_sysinfo))
    app.add_handler(CommandHandler("briefing",   cmd_briefing))
    app.add_handler(CommandHandler("remind",     cmd_remind))
    app.add_handler(CommandHandler("reminders",  cmd_reminders))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    bridge.on_notification(lambda t, uid: _on_notification(t, uid, app.bot))

    return app, _BOT_COMMANDS


# ── Reminder loop ─────────────────────────────────────────────────────────────

_tasks: list[asyncio.Task] = []


async def _reminder_loop(bot):
    from datetime import datetime
    from telegram_bot.reminders import get_due, mark_sent
    while True:
        try:
            await asyncio.sleep(30)
            for r in get_due(datetime.now()):
                try:
                    await bot.send_message(chat_id=r["user_id"],
                                           text=f"🔔 Напоминание: {r['text']}")
                    mark_sent(r["id"])
                except Exception as e:
                    logger.error(f"Reminder send: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Reminder loop: {e}")


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_app
    logger.info("Render app starting…")

    _tg_app, bot_commands = await _build_tg_app()
    await _tg_app.initialize()
    await _tg_app.start()
    await _tg_app.bot.set_my_commands(bot_commands)

    if cfg.miniapp_url:
        webhook_url = f"{cfg.miniapp_url}/webhook/{cfg.telegram_token}"
        await _tg_app.bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "edited_message", "callback_query"],
        )
        logger.info(f"Webhook registered: {cfg.miniapp_url}/webhook/***")
    else:
        logger.warning(
            "MINIAPP_URL not set — webhook NOT registered. "
            "Set MINIAPP_URL in Render env to https://<your-app>.onrender.com"
        )

    _tasks.append(asyncio.create_task(_reminder_loop(_tg_app.bot)))
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


@miniapp_server.app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    """Telegram calls this endpoint for every update."""
    if token != cfg.telegram_token:
        return JSONResponse({"ok": False}, status_code=403)
    if _tg_app is None:
        return JSONResponse({"ok": False}, status_code=503)
    data = await request.json()
    update = Update.de_json(data, _tg_app.bot)
    await _tg_app.process_update(update)
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
