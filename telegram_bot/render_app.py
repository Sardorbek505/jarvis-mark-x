"""
Render entry point — runs the Telegram bot + Mini App server in one process.

On Render, a "web service" must bind to $PORT.
The FastAPI app does that; the Telegram bot runs as a background asyncio task.
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_bot.config import load as load_config
from telegram_bot.gemini_client import GeminiClient
from telegram_bot.pc_bridge import PCBridge
from telegram_bot import miniapp_server  # imports the FastAPI `app` + sets _gemini/_bridge

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("jarvis-render")

cfg = load_config()

# Shared instances
gemini = GeminiClient(cfg.gemini_api_key, cfg.gemini_model)
bridge = PCBridge()

# Wire into miniapp_server
miniapp_server._gemini = gemini
miniapp_server._bridge = bridge
bridge.on_status_change(miniapp_server.broadcast_pc_status)


# ── Telegram bot (inline, no separate event loop) ─────────────────────────────

async def _run_bot():
    """Run Telegram bot inside the existing asyncio event loop."""
    from datetime import datetime
    from telegram import BotCommand
    from telegram.ext import (
        ApplicationBuilder, CommandHandler, MessageHandler, filters
    )
    from telegram_bot.reminders import get_due, mark_sent, list_reminders, parse_reminder, add_reminder
    from telegram_bot.bot import (
        cmd_start, cmd_help, cmd_app, cmd_status, cmd_clear,
        cmd_pc, cmd_screenshot, cmd_vol, cmd_lock, cmd_sysinfo, cmd_briefing,
        cmd_remind, cmd_reminders,
        handle_text, handle_voice, handle_photo,
        _on_notification, _BOT_COMMANDS,
    )

    app = (
        ApplicationBuilder()
        .token(cfg.telegram_token)
        .build()
    )

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("app",        cmd_app))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("pc",         cmd_pc))
    app.add_handler(CommandHandler("screenshot", cmd_screenshot))
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

    async def reminder_loop():
        while True:
            try:
                await asyncio.sleep(30)
                due = get_due(datetime.now())
                for r in due:
                    try:
                        await app.bot.send_message(chat_id=r["user_id"],
                                                   text=f"🔔 Напоминание: {r['text']}")
                        mark_sent(r["id"])
                    except Exception as e:
                        logger.error(f"Reminder: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reminder loop: {e}")

    await app.initialize()
    await app.bot.set_my_commands(_BOT_COMMANDS)
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    asyncio.create_task(reminder_loop())
    logger.info("Telegram bot started (inline) ✅")


# ── FastAPI lifespan — start everything on uvicorn boot ───────────────────────

_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Render app starting…")
    # PC bridge is passive (PCs dial in via /pc-link) — no connect loop needed.
    _tasks.append(asyncio.create_task(_run_bot()))
    yield
    for t in _tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    logger.info("Render app stopped.")


# ── Build the combined FastAPI app ────────────────────────────────────────────

# Re-use miniapp_server's `app` but attach our lifespan
miniapp_server.app.router.lifespan_context = lifespan


@miniapp_server.app.get("/health")
async def health():
    """Keep-alive endpoint — ping this every 5 min to prevent Render sleep."""
    return JSONResponse({"status": "ok", "pc": bridge.connected})


# Export for uvicorn
app = miniapp_server.app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", cfg.miniapp_port or 8000))
    uvicorn.run("telegram_bot.render_app:app", host="0.0.0.0", port=port, log_level="info")
