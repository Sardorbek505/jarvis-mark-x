"""
Render entry point — Telegram bot (webhook) + Mini App server in one process.

Webhook mode eliminates telegram.error.Conflict — no more polling fights between
deploys. Telegram POSTs updates to /telegram-webhook with a secret header;
FastAPI feeds them into the Application's update queue.
"""
import asyncio
import contextlib
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
from telegram_bot import proactive
from telegram_bot import context_builder
from telegram_bot import recall
from telegram_bot import memory_rag
from telegram_bot.memory_store import MemoryStore

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("jarvis-render")

# httpx логирует полный URL каждого запроса, а в путях Telegram API токен бота
# идёт прямо в URL (/bot<TOKEN>/setWebhook) — на INFO он утекал в логи Space
# при каждом вызове. Ошибки транспорта поднимает сам telegram-пакет.
logging.getLogger("httpx").setLevel(logging.WARNING)

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
    # Single source of truth — shared with bot.py (see context_builder.py).
    return context_builder.build_context(memory, cfg, uid)


gemini.set_context_provider(_build_context)
gemini.set_recall_provider(memory_rag.make_recall_provider(memory, gemini, recall))

# Wire into miniapp_server
miniapp_server._gemini = gemini
miniapp_server._bridge = bridge
miniapp_server._memory = memory


# ── PC online/offline → keep Mini App in sync AND ping the user in Telegram ────
_pc_online_notified = False
_pc_offline_task = None


async def _notify_users(text: str):
    if _tg_app is None:
        return
    for uid in cfg.allowed_user_ids:
        try:
            await _tg_app.bot.send_message(chat_id=uid, text=text)
        except Exception as e:
            logger.debug(f"notify {uid}: {e}")


async def _flush_outbox():
    """PC just came online — deliver any messages queued while it was off."""
    if _tg_app is None:
        return
    try:
        pending = await memory.pending_outbound()
    except Exception as e:
        logger.debug(f"outbox read: {e}")
        return
    for item in pending:
        res = await bridge.send_userbot(item["target"], item["message"], item["as_voice"], item["user_id"])
        if res and "Отправлено" in (res.get("text", "")):
            await memory.delete_outbound(item["id"])
            try:
                await _tg_app.bot.send_message(
                    chat_id=item["user_id"], text=f"📤 Из очереди отправлено {item['alias']}."
                )
            except Exception as exc:
                logger.debug("Подавлено исключение: %s", exc, exc_info=True)
            await asyncio.sleep(1)   # gentle pacing, avoid spammy bursts
        else:
            break   # PC dropped again — leave the rest queued for next time


async def _on_pc_status(online: bool):
    await miniapp_server.broadcast_pc_status(online)   # Mini App badge
    global _pc_online_notified, _pc_offline_task
    if online:
        if _pc_offline_task:
            _pc_offline_task.cancel()
            _pc_offline_task = None
        if not _pc_online_notified:
            _pc_online_notified = True
            await _notify_users("🖥 ПК онлайн — можно управлять компьютером.")
        asyncio.create_task(_flush_outbox())   # deliver anything queued while offline
    else:
        # Debounce: brief reconnects flap online/offline. Only announce offline
        # after 20s without a reconnect, so we don't spam on network blips.
        if _pc_offline_task:
            return

        async def _confirm_offline():
            global _pc_online_notified, _pc_offline_task
            try:
                await asyncio.sleep(20)
            except asyncio.CancelledError:
                return
            _pc_offline_task = None
            if not bridge.connected:
                _pc_online_notified = False
                await _notify_users("🌙 ПК офлайн.")

        _pc_offline_task = asyncio.create_task(_confirm_offline())


bridge.on_status_change(_on_pc_status)


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
        cmd_contacts, cmd_addcontact, cmd_delcontact,
        cmd_notes, cmd_note, cmd_delnote, cmd_findnote,
        cmd_schedule, cmd_clearschedule, cmd_projects, cmd_delproject,
        cmd_pc, cmd_screenshot, cmd_camera, cmd_vol, cmd_lock, cmd_sysinfo, cmd_briefing,
        cmd_remind, cmd_reminders, cmd_task, cmd_tasks, cmd_today, cmd_done,
        cmd_habit, cmd_habits, cmd_check,
        cmd_morning, cmd_evening, cmd_mode, cmd_profile, cmd_memstats, cmd_reindex,
        cmd_journal,
        cmd_ask, cmd_curiosity, cmd_remember, cmd_forget,
        on_callback,
        handle_text, handle_voice, handle_photo, handle_document,
        _on_notification, _BOT_COMMANDS,
    )

    builder = (
        ApplicationBuilder()
        .token(cfg.telegram_token)
        .updater(None)          # disable polling/updater — we receive via webhook
        # Free CPU hosts (HF Spaces) have a slow first outbound call — give the
        # initial get_me/setWebhook room instead of timing out the whole startup.
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
    )

    # HF Spaces block outbound to api.telegram.org. Route the bot's API + file
    # calls through a Cloudflare Worker proxy when TELEGRAM_API_BASE is set
    # (e.g. https://jarvis-tg-proxy.<sub>.workers.dev). Webhook delivery is
    # inbound (Telegram -> our public URL) so it is unaffected.
    api_base = os.getenv("TELEGRAM_API_BASE", "").strip().rstrip("/")
    if api_base:
        builder = builder.base_url(f"{api_base}/bot").base_file_url(f"{api_base}/file/bot")
        logger.info(f"Telegram API routed via proxy: {api_base}")

    app = builder.build()

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("app",        cmd_app))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("contacts",   cmd_contacts))
    app.add_handler(CommandHandler("addcontact", cmd_addcontact))
    app.add_handler(CommandHandler("delcontact", cmd_delcontact))
    app.add_handler(CommandHandler("notes",      cmd_notes))
    app.add_handler(CommandHandler("note",       cmd_note))
    app.add_handler(CommandHandler("delnote",    cmd_delnote))
    app.add_handler(CommandHandler("findnote",   cmd_findnote))
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
    app.add_handler(CommandHandler("schedule",   cmd_schedule))
    app.add_handler(CommandHandler("clearschedule", cmd_clearschedule))
    app.add_handler(CommandHandler("projects",   cmd_projects))
    app.add_handler(CommandHandler("delproject", cmd_delproject))
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
    app.add_error_handler(_on_error)

    bridge.on_notification(lambda t, uid: _on_notification(t, uid, app.bot))

    return app, _BOT_COMMANDS


async def _on_error(update, context) -> None:
    """Last line of defence: never let a handler crash silently. Log full
    traceback server-side and tell the user something broke (item #3)."""
    logger.exception("Unhandled handler error: %s", context.error)
    chat_id = None
    try:
        if update is not None and getattr(update, "effective_chat", None):
            chat_id = update.effective_chat.id
    except Exception:
        chat_id = None
    if chat_id is None:
        return
    with contextlib.suppress(Exception):
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Что-то сломалось на моей стороне — я уже записал ошибку. "
                 "Попробуй ещё раз через минуту или переформулируй.",
        )


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


_TG_BOOT_MIN_SEC = 30.0
_TG_BOOT_MAX_SEC = 300.0

# Hosting platforms block outbound traffic differently and change the rules
# without notice (HF started dropping both api.telegram.org and *.workers.dev).
# When Telegram will not connect, `DIAG_EGRESS=1` answers the only question that
# matters: which host can this container still reach, i.e. where to put a proxy.
_EGRESS_PROBES = (
    "https://api.telegram.org",
    "https://jarvis-tg-proxy.060910501297.workers.dev",
    "https://vercel.com",
    "https://deno.com",
    "https://api.render.com",
    "https://pypi.org",
)


async def _probe_egress():
    """Log which hosts are reachable. Runs once, only when DIAG_EGRESS is set."""
    import httpx
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
        for url in _EGRESS_PROBES:
            try:
                r = await client.head(url)
                logger.info(f"[egress] OK   {url} -> HTTP {r.status_code}")
            except Exception as e:
                logger.info(f"[egress] FAIL {url} -> {type(e).__name__}: {e}")


async def _telegram_boot():
    """Bring Telegram up, retrying forever. Everything that needs the bot lives
    here, so an unreachable Telegram API costs the bot — not the whole app."""
    global _tg_app
    delay = _TG_BOOT_MIN_SEC
    attempt = 0
    while True:
        attempt += 1
        try:
            _tg_app, bot_commands = await _build_tg_app()
            await _tg_app.initialize()
            await _tg_app.start()
            await _tg_app.bot.set_my_commands(bot_commands)
            miniapp_server._bot = _tg_app.bot   # Mini App pushes photos to the chat

            if cfg.miniapp_url:
                webhook_url = f"{cfg.miniapp_url.rstrip('/')}{_WEBHOOK_PATH}"
                await _set_webhook(_tg_app.bot, webhook_url, drop_pending=True)
                logger.info(f"Webhook registered: {webhook_url}")
                _tasks.append(asyncio.create_task(_webhook_keeper(_tg_app.bot, webhook_url)))
            else:
                logger.warning(
                    "MINIAPP_URL not set — webhook NOT registered. "
                    "Set MINIAPP_URL to https://<your-app>.hf.space"
                )

            _tasks.append(asyncio.create_task(_reminder_loop(_tg_app.bot)))
            _tasks.append(asyncio.create_task(
                proactive.loop(_tg_app.bot, gemini, memory, cfg.timezone, cfg.default_city)
            ))
            logger.info(f"JARVIS started ✅ (webhook mode, попытка №{attempt})")
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _tg_app = None      # webhook route answers 503 until we are up
            if attempt == 1 and os.getenv("DIAG_EGRESS", "").strip():
                await _probe_egress()
            logger.error(
                f"Telegram недоступен (попытка №{attempt}): {type(e).__name__}: {e}. "
                f"Повтор через {round(delay)} с. ПК-линк и Mini App работают."
            )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, _TG_BOOT_MAX_SEC)


# ── FastAPI lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_app
    logger.info("Render app starting…")

    # init() never raises: a dead DB degrades memory, it must not kill the app
    # (was: asyncpg quota error -> lifespan crash -> Exit code 3 -> Space down).
    await memory.init()
    _tasks.append(asyncio.create_task(memory.watch()))

    # Telegram is brought up in the background: when its API is unreachable
    # (blocked egress, dead proxy) the PC link and the Mini App must still
    # serve. Previously 5 failed attempts re-raised -> Exit code 3 -> the whole
    # Space died, taking down parts that never needed Telegram at all.
    _tasks.append(asyncio.create_task(_telegram_boot()))
    logger.info("Render app started ✅ (Telegram connects in background)")

    yield

    for t in _tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    # _tg_app stays None while Telegram is unreachable — shutdown must cope.
    if _tg_app is not None:
        if cfg.miniapp_url:
            try:
                await _tg_app.bot.delete_webhook()
            except Exception as exc:
                logger.debug("Подавлено исключение: %s", exc, exc_info=True)
        await _tg_app.stop()
        await _tg_app.shutdown()
    await memory.close()
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
