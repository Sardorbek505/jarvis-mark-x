"""FastAPI WebSocket server for the JARVIS Mini App (runs on VPS/Render with the bot)."""
import asyncio
import base64
import json
import logging
import os
import re
import struct
import sys
from pathlib import Path
from typing import Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_bot import user_context
from telegram_bot import agenda
from telegram_bot import weather
from telegram_bot import reminders as rem
from telegram_bot import directives

logger = logging.getLogger("jarvis-miniapp")

MINIAPP_DIR = Path(__file__).parent / "miniapp"
_DEFAULT_TZ = os.getenv("TIMEZONE", "Asia/Almaty")
_DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Шымкент")

# Shared secret — the home PC must present this to link. Set via env on Render.
PC_LINK_TOKEN = os.getenv("PC_LINK_TOKEN", "")

_PC_KEYWORDS = [
    "play", "stop", "pause", "next", "prev", "volume",
    "включи", "выключи", "стоп", "пауза", "следующий", "предыдущий", "трек",
    "поставь", "запусти", "воспроизведи", "играй",
    "open", "открой", "weather", "погода",
    "search", "найди", "поищи",
    "сверни", "свернуть", "minimize", "рабочий стол", "разверни",
    "закрой окно", "переключи окно", "проводник", "диспетчер",
    "screenshot", "скриншот", "заблокируй", "sysinfo", "системная",
    "переключи", "отключи", "громче", "тише", "дальше", "громкость",
    "камер", "вебкам", "webcam", "сфоткай", "что рядом", "что вокруг", "что там происходит",
    "брифинг", "briefing", "батарея", "calendar", "календарь",
    "разблокир", "нажми enter", "нажать enter", "нажми интер",
]


def _looks_like_pc_command(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _PC_KEYWORDS)


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw int16 PCM in a WAV container."""
    n_ch, bits = 1, 16
    data_size = len(pcm_bytes)
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, 1, n_ch, sample_rate,
        sample_rate * n_ch * bits // 8,
        n_ch * bits // 8, bits,
        b'data', data_size,
    )
    return header + pcm_bytes


app = FastAPI(title="JARVIS Mini App")

# Lazy references set by render_app.py / __main__
_gemini = None
_bridge = None
_memory = None
_bot = None      # PTB bot — lets the Mini App push screenshots/photos into the TG chat
_audio_buffers: Dict[int, bytes] = {}
_miniapp_clients: Set[WebSocket] = set()


# ── Static files ──────────────────────────────────────────────────────────────
# Telegram's in-app webview caches static assets aggressively, so changes never
# show up. Force revalidation on every load with no-store.
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@app.get("/")
async def serve_index():
    return FileResponse(MINIAPP_DIR / "index.html", headers=_NOCACHE)

@app.get("/app.js")
async def serve_appjs():
    return FileResponse(MINIAPP_DIR / "app.js", media_type="application/javascript", headers=_NOCACHE)

@app.get("/style.css")
async def serve_css():
    return FileResponse(MINIAPP_DIR / "style.css", media_type="text/css", headers=_NOCACHE)

@app.get("/ping")
async def ping():
    # Ultra-light keep-alive target for cron-job.org (no file I/O, tiny payload).
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("ok", headers=_NOCACHE)

@app.get("/worklet.js")
async def serve_worklet():
    return FileResponse(MINIAPP_DIR / "worklet.js", media_type="application/javascript", headers=_NOCACHE)


# ── PC status broadcast to Mini App clients ───────────────────────────────────

async def broadcast_pc_status(online: bool):
    dead = []
    for ws in list(_miniapp_clients):
        try:
            await ws.send_text(json.dumps({"type": "pc_status", "online": online}))
        except Exception:
            dead.append(ws)
    for ws in dead:
        _miniapp_clients.discard(ws)


# ── PC link — home PC connects OUT to here (works behind NAT) ──────────────────

@app.websocket("/pc-link")
async def pc_link(ws: WebSocket):
    token = ws.query_params.get("token", "")
    if PC_LINK_TOKEN and token != PC_LINK_TOKEN:
        await ws.close(code=1008)
        logger.warning("PC link rejected — bad token")
        return
    await ws.accept()
    cid = await _bridge.register(ws) if _bridge else None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if _bridge:
                await _bridge.handle_message(msg)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"pc-link error: {e}")
    finally:
        if _bridge and cid is not None:
            await _bridge.unregister(cid)


# ── Mini App data tabs (habits / tasks / reminders / dashboard) ───────────────

def _today_iso(user_id: int) -> str:
    return user_context.local_now(user_id, _DEFAULT_TZ).date().isoformat()


async def _build_view(user_id: int, view: str) -> dict:
    """Assemble the payload for a Mini App data tab from the durable store."""
    if not _memory:
        return {"error": "memory offline"}
    await _memory.ensure_loaded(user_id)
    tz = user_context.local_now(user_id, _DEFAULT_TZ).tzinfo

    if view == "habits":
        return {"habits": await _memory.get_habits(user_id, _today_iso(user_id))}

    if view == "tasks":  # «Дела» — tasks + reminders together
        tasks = await _memory.get_tasks(user_id)
        reminders = await _memory.list_reminders(user_id)
        return {
            "tasks": [
                {"id": t["id"], "title": t["title"],
                 "due": agenda.fmt_due(t["due"]) if t.get("due") else "",
                 "overdue": bool(t.get("due") and agenda.is_overdue(t["due"]))}
                for t in tasks
            ],
            "reminders": [
                {"id": r["id"], "text": r["text"], "when": rem.fmt_local(r["due"], tz)}
                for r in reminders
            ],
        }

    if view == "dashboard":
        tasks = await _memory.get_tasks(user_id)
        today = [t for t in tasks if t.get("due") and agenda.is_today(t["due"])]
        habits = await _memory.get_habits(user_id, _today_iso(user_id))
        reminders = await _memory.list_reminders(user_id)
        profile = await _memory.get_profile(user_id)
        city = user_context.get_city(user_id, _DEFAULT_CITY)
        wx = await weather.for_city(city) if city else None
        return {
            "name": profile.get("name", ""),
            "city": city,
            "weather": wx or "",
            "open_tasks": len(tasks),
            "today_tasks": [t["title"] for t in today][:5],
            "habits_done": sum(1 for h in habits if h["done_today"]),
            "habits_total": len(habits),
            "best_streak": max([h["streak"] for h in habits], default=0),
            "next_reminder": (
                f"{reminders[0]['text']} — {rem.fmt_local(reminders[0]['due'], tz)}"
                if reminders else ""
            ),
        }

    return {"error": f"unknown view {view}"}


async def _send_view(ws: WebSocket, user_id: int, view: str):
    payload = await _build_view(user_id, view)
    await ws.send_text(json.dumps({"type": "data", "view": view, "payload": payload}))


async def _handle_action(ws: WebSocket, user_id: int, msg: dict):
    """Mutations from data tabs, then echo back the refreshed view."""
    if not _memory:
        return
    await _memory.ensure_loaded(user_id)
    mtype = msg.get("type")

    if mtype == "habit_add" and msg.get("title"):
        await _memory.add_habit(user_id, msg["title"].strip())
        await _send_view(ws, user_id, "habits")
    elif mtype == "habit_toggle" and msg.get("id") is not None:
        await _memory.toggle_habit(user_id, int(msg["id"]), _today_iso(user_id))
        await _send_view(ws, user_id, "habits")
    elif mtype == "habit_delete" and msg.get("id") is not None:
        await _memory.delete_habit(user_id, int(msg["id"]))
        await _send_view(ws, user_id, "habits")
    elif mtype == "task_add" and msg.get("text"):
        due, title = agenda.parse(msg["text"].strip())
        await _memory.add_task(user_id, title, due)
        await _send_view(ws, user_id, "tasks")
    elif mtype == "task_done" and msg.get("id") is not None:
        await _memory.complete_task(user_id, int(msg["id"]))
        await _send_view(ws, user_id, "tasks")
    elif mtype == "reminder_add" and msg.get("text"):
        now_local = user_context.local_now(user_id, _DEFAULT_TZ)
        parsed = rem.parse_reminder(msg["text"].strip(), now_local)
        if parsed:
            when, what = parsed
            await _memory.add_reminder(user_id, what, rem.to_utc_iso(when))
        await _send_view(ws, user_id, "tasks")


# ── Mini App clients (browser / Telegram) ─────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _miniapp_clients.add(ws)
    try:
        user_id = int(ws.query_params.get("user_id", 0) or 0)
    except ValueError:
        user_id = 0

    _audio_buffers[user_id] = b""

    await ws.send_text(json.dumps({
        "type": "pc_status",
        "online": _bridge.connected if _bridge else False,
    }))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type", "")

            if mtype == "client_info":
                # Phone reports its timezone + location so JARVIS knows where
                # the user is and the correct local time — wherever they travel.
                user_context.update(
                    user_id,
                    tz=msg.get("tz"),
                    city=msg.get("city"),
                    lat=msg.get("lat"),
                    lon=msg.get("lon"),
                )
                continue

            if mtype == "get_data":
                await _send_view(ws, user_id, msg.get("view", "dashboard"))
                continue

            if mtype in ("habit_add", "habit_toggle", "habit_delete",
                         "task_add", "task_done", "reminder_add"):
                await _handle_action(ws, user_id, msg)
                continue

            if mtype == "text":
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                want_audio = bool(msg.get("tts", True))
                await ws.send_text(json.dumps({"type": "thinking"}))
                await _handle_text(ws, user_id, text, want_audio=want_audio)

            elif mtype == "start_voice":
                _audio_buffers[user_id] = b""
                await ws.send_text(json.dumps({"type": "status", "state": "listening"}))

            elif mtype == "audio":
                chunk_b64 = msg.get("data", "")
                if chunk_b64:
                    # get/set (not +=) so hands-free streaming never KeyErrors
                    # after a segment boundary reset the buffer.
                    _audio_buffers[user_id] = _audio_buffers.get(user_id, b"") + base64.b64decode(chunk_b64)

            elif mtype == "stop_voice":
                want_audio = bool(msg.get("tts", True))
                # Reset (not pop) so continued streaming keeps a valid buffer.
                pcm = _audio_buffers.get(user_id, b"")
                _audio_buffers[user_id] = b""
                if len(pcm) >= 3200:
                    await ws.send_text(json.dumps({"type": "status", "state": "processing"}))
                    await _handle_voice(ws, user_id, pcm, want_audio=want_audio)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WS error uid={user_id}: {e}")
    finally:
        _miniapp_clients.discard(ws)
        _audio_buffers.pop(user_id, None)


_SPEECH_STRIP = re.compile(
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    r"\U00002190-\U000021FF\U00002B00-\U00002BFF]"
)


def _clean_for_speech(text: str) -> str:
    """Strip emoji / markdown / urls so the spoken text sounds natural."""
    if not text:
        return ""
    t = _SPEECH_STRIP.sub("", text)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>•►]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


async def _send_text(ws: WebSocket, text: str, want_audio: bool):
    """Send a text bubble and, if wanted, the same text spoken in the JARVIS
    voice (Charon) via Gemini TTS — same voice as the desktop app."""
    await ws.send_text(json.dumps({"type": "text", "text": text}))
    if want_audio and _gemini:
        spoken = _clean_for_speech(text)
        pcm = None
        if spoken:
            try:
                pcm = await _gemini.synthesize_speech(spoken)
            except Exception as e:
                logger.debug(f"TTS: {e}")
                pcm = None
        if pcm:
            await ws.send_text(json.dumps({
                "type": "audio",
                "data": base64.b64encode(pcm).decode(),
            }))
        else:
            # Real voice unavailable — tell the client explicitly so it can use
            # its browser-TTS fallback. No timers, so the two never overlap.
            await ws.send_text(json.dumps({"type": "tts_failed"}))


async def _send_media_to_tg(ws: WebSocket, user_id: int, pc_cmd: str, label: str):
    """Grab a screenshot/camera shot from the PC and push it to the user's
    Telegram chat via the bot (works on HF — bot uses the Cloudflare proxy)."""
    if not (_bridge and _bridge.connected):
        await _send_text(ws, "❌ ПК офлайн — нечего отправлять.", want_audio=False)
        return
    if _bot is None:
        await _send_text(ws, "❌ Отправка в Telegram недоступна.", want_audio=False)
        return
    await _send_text(ws, f"📸 Делаю {label} и отправляю в Telegram…", want_audio=False)
    rich = await _bridge.send_command_full(pc_cmd, user_id)
    img = (rich or {}).get("image_b64")
    if not img:
        await _send_text(ws, "❌ Не получил изображение с ПК.", want_audio=False)
        return
    try:
        from io import BytesIO
        data = base64.b64decode(img)
        await _bot.send_photo(chat_id=user_id, photo=BytesIO(data),
                              caption=(rich.get("text") or label))
        await _send_text(ws, "📤 Отправил в Telegram-чат ✅", want_audio=False)
    except Exception as e:
        logger.error(f"send media to tg: {e}")
        await _send_text(ws, f"❌ Не смог отправить в Telegram: {e}", want_audio=False)


async def _handle_text(ws: WebSocket, user_id: int, text: str, want_audio: bool = True):
    low = text.lower()
    # "Скачать в Telegram" buttons: send the screenshot / camera shot to the chat.
    if ("в телеграм" in low or "в тг" in low or "to tg" in low or "→ telegram" in low):
        if any(k in low for k in ("камер", "фото", "camera")):
            await _send_media_to_tg(ws, user_id, "снимок с камеры", "снимок с камеры")
        else:
            await _send_media_to_tg(ws, user_id, "скриншот", "скриншот")
        return

    is_pc_cmd = _looks_like_pc_command(text)

    if _bridge and _bridge.connected and is_pc_cmd:
        rich = await _bridge.send_command_full(text, user_id)
        if rich:
            if rich.get("image_b64"):
                await ws.send_text(json.dumps({
                    "type": "image",
                    "data": rich["image_b64"],
                    "caption": rich.get("text", ""),
                }))
            if rich.get("text"):
                # PC command results are short status confirmations — show as text,
                # don't speak them (avoids the browser-fallback voice on button taps).
                await _send_text(ws, f"🖥 {rich['text']}", want_audio=False)
        else:
            # PC connected but didn't respond — never fall through to Gemini
            await _send_text(
                ws,
                "❌ ПК не ответил. Убедись что pc_server запущен на компьютере (`scripts\\start_pc.bat`).",
                want_audio=False,
            )
        return

    if _bridge and not _bridge.connected and is_pc_cmd:
        await _send_text(
            ws,
            "❌ ПК офлайн. Запусти `scripts\\start_pc.bat` на своём компьютере.",
            want_audio=False,
        )
        return

    # Not a PC command — send to Gemini (with long-term memory)
    if _gemini:
        if _memory:
            await _memory.ensure_loaded(user_id)
        reply = await _gemini.chat(user_id, text)
        # Execute any hidden reminder/habit/task directives → durable store
        if _memory:
            tz = user_context.local_now(user_id, _DEFAULT_TZ).tzinfo
            reply, summary = await directives.apply(_memory, user_id, reply, tz)
            if summary:
                reply += "\n\n✅ Добавил — " + ", ".join(summary)
            asyncio.create_task(_memory.observe(user_id, _gemini, text, reply))
    else:
        reply = "AI-сервис недоступен."
    await _send_text(ws, reply, want_audio)


async def _handle_voice(ws: WebSocket, user_id: int, pcm: bytes, want_audio: bool = True):
    if not _gemini:
        await ws.send_text(json.dumps({"type": "text", "text": "AI-сервис недоступен."}))
        return
    if len(pcm) < 3200:
        await ws.send_text(json.dumps({
            "type": "text",
            "text": "Не услышал. Нажми кнопку, говори, потом нажми ещё раз."
        }))
        return
    try:
        wav = _pcm_to_wav(pcm)
        # Transcribe first, then run the recognised text through the SAME pipeline
        # as typed text — so voice commands control the PC, not just chat.
        transcript = await _gemini.transcribe(wav, mime_type="audio/wav")
        if not transcript:
            await ws.send_text(json.dumps({
                "type": "text",
                "text": "Не разобрал, что ты сказал. Попробуй ещё раз чуть чётче."
            }))
            return
        # Show the user what was recognised
        await ws.send_text(json.dumps({"type": "transcript_user", "text": transcript}))
        # Voice input → speak the reply by default
        await _handle_text(ws, user_id, transcript, want_audio=want_audio)
    except Exception as e:
        logger.error(f"Voice handle error: {e}")
        await ws.send_text(json.dumps({
            "type": "text",
            "text": "Не смог обработать голос. Попробуй ещё раз."
        }))


# ── Entry points ──────────────────────────────────────────────────────────────

async def run(port: int = 8000, gemini=None, bridge=None):
    """Start server within an existing asyncio loop."""
    global _gemini, _bridge
    if gemini is not None:
        _gemini = gemini
    if bridge is not None:
        _bridge = bridge
        _bridge.on_status_change(broadcast_pc_status)

    import uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    from telegram_bot.config import load as load_config
    from telegram_bot.gemini_client import GeminiClient
    from telegram_bot.pc_bridge import PCBridge

    cfg = load_config()
    _gemini = GeminiClient(cfg.gemini_api_key, cfg.gemini_model)
    _bridge = PCBridge()
    _bridge.on_status_change(broadcast_pc_status)

    asyncio.run(run(port=cfg.miniapp_port))
