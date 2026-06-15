"""FastAPI WebSocket server for the JARVIS Mini App (runs on VPS/Render with the bot)."""
import asyncio
import base64
import json
import logging
import os
import struct
import sys
from pathlib import Path
from typing import Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram_bot import user_context

logger = logging.getLogger("jarvis-miniapp")

MINIAPP_DIR = Path(__file__).parent / "miniapp"

# Shared secret — the home PC must present this to link. Set via env on Render.
PC_LINK_TOKEN = os.getenv("PC_LINK_TOKEN", "")

_PC_KEYWORDS = [
    "play", "stop", "pause", "next", "prev", "volume",
    "включи", "выключи", "стоп", "пауза", "следующий",
    "поставь", "запусти", "воспроизведи", "играй",
    "open", "открой", "weather", "погода",
    "search", "найди", "поищи",
    "сверни", "свернуть", "minimize", "рабочий стол", "разверни",
    "закрой окно", "переключи окно", "проводник", "диспетчер",
    "screenshot", "скриншот", "заблокируй", "sysinfo", "системная",
    "переключи", "отключи", "громче", "тише", "дальше", "громкость",
    "брифинг", "briefing", "батарея", "calendar", "календарь",
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
_audio_buffers: Dict[int, bytes] = {}
_miniapp_clients: Set[WebSocket] = set()


# ── Static files ──────────────────────────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse(MINIAPP_DIR / "index.html")

@app.get("/app.js")
async def serve_appjs():
    return FileResponse(MINIAPP_DIR / "app.js", media_type="application/javascript")

@app.get("/style.css")
async def serve_css():
    return FileResponse(MINIAPP_DIR / "style.css", media_type="text/css")

@app.get("/worklet.js")
async def serve_worklet():
    return FileResponse(MINIAPP_DIR / "worklet.js", media_type="application/javascript")


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

            if mtype == "text":
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                await ws.send_text(json.dumps({"type": "thinking"}))
                await _handle_text(ws, user_id, text)

            elif mtype == "start_voice":
                _audio_buffers[user_id] = b""
                await ws.send_text(json.dumps({"type": "status", "state": "listening"}))

            elif mtype == "audio":
                chunk_b64 = msg.get("data", "")
                if chunk_b64:
                    _audio_buffers[user_id] += base64.b64decode(chunk_b64)

            elif mtype == "stop_voice":
                await ws.send_text(json.dumps({"type": "status", "state": "processing"}))
                pcm = _audio_buffers.pop(user_id, b"")
                await _handle_voice(ws, user_id, pcm)
                await ws.send_text(json.dumps({"type": "status", "state": "idle"}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WS error uid={user_id}: {e}")
    finally:
        _miniapp_clients.discard(ws)
        _audio_buffers.pop(user_id, None)


async def _handle_text(ws: WebSocket, user_id: int, text: str):
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
                await ws.send_text(json.dumps({
                    "type": "text",
                    "text": f"🖥 {rich['text']}",
                }))
        else:
            # PC connected but didn't respond — never fall through to Gemini
            await ws.send_text(json.dumps({
                "type": "text",
                "text": "❌ ПК не ответил. Убедись что pc_server запущен на компьютере (`scripts\\start_pc.bat`).",
            }))
        return

    if _bridge and not _bridge.connected and is_pc_cmd:
        await ws.send_text(json.dumps({
            "type": "text",
            "text": "❌ ПК офлайн. Запусти `scripts\\start_pc.bat` на своём компьютере.",
        }))
        return

    # Not a PC command — send to Gemini
    if _gemini:
        reply = await _gemini.chat(user_id, text)
    else:
        reply = "AI-сервис недоступен."
    await ws.send_text(json.dumps({"type": "text", "text": reply}))


async def _handle_voice(ws: WebSocket, user_id: int, pcm: bytes):
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
        await _handle_text(ws, user_id, transcript)
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
