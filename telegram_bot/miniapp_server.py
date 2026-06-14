"""FastAPI WebSocket server for the JARVIS Mini App (runs on VPS alongside the bot)."""
import asyncio
import base64
import json
import logging
import struct
import sys
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("jarvis-miniapp")

MINIAPP_DIR = Path(__file__).parent / "miniapp"

_PC_KEYWORDS = [
    "play", "stop", "pause", "next", "prev", "volume",
    "включи", "выключи", "стоп", "пауза", "следующий",
    "поставь", "запусти", "воспроизведи", "играй",
    "open", "открой", "weather", "погода",
    "search", "найди", "поищи",
    "сверни", "свернуть", "minimize", "рабочий стол", "разверни",
    "закрой окно", "переключи окно", "проводник", "диспетчер",
    "screenshot", "скриншот", "заблокируй", "sysinfo", "системная",
    "переключи", "отключи", "громче", "тише", "дальше",
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

# Lazy references set by run() or __main__
_gemini = None
_bridge = None
_audio_buffers: Dict[int, bytes] = {}


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


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        user_id = int(ws.query_params.get("user_id", 0) or 0)
    except ValueError:
        user_id = 0

    _audio_buffers[user_id] = b""

    # Send initial status
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
        _audio_buffers.pop(user_id, None)


async def _handle_text(ws: WebSocket, user_id: int, text: str):
    sent = False

    # Try PC bridge first
    if _bridge and _bridge.connected and _looks_like_pc_command(text):
        rich = await _bridge.send_command_full(text, user_id)
        if rich:
            if rich.get("image_b64"):
                await ws.send_text(json.dumps({
                    "type": "image",
                    "data": rich["image_b64"],
                    "caption": rich.get("text", ""),
                }))
                sent = True
            if rich.get("text"):
                await ws.send_text(json.dumps({
                    "type": "text",
                    "text": f"🖥 {rich['text']}",
                }))
                sent = True

    # Fall through to Gemini
    if not sent:
        if _gemini:
            reply = await _gemini.chat(user_id, text)
        else:
            reply = "AI-сервис недоступен."
        await ws.send_text(json.dumps({"type": "text", "text": reply}))


async def _handle_voice(ws: WebSocket, user_id: int, pcm: bytes):
    if not _gemini:
        await ws.send_text(json.dumps({"type": "text", "text": "AI-сервис недоступен."}))
        return
    if len(pcm) < 3200:  # < 100ms
        await ws.send_text(json.dumps({"type": "text", "text": "Не услышал. Нажми и удерживай кнопку пока говоришь."}))
        return
    try:
        wav = _pcm_to_wav(pcm)
        reply = await _gemini.chat_with_audio(user_id, wav, mime_type="audio/wav")
    except Exception as e:
        logger.error(f"Voice handle error: {e}")
        reply = "Не смог обработать голос. Попробуй ещё раз."
    await ws.send_text(json.dumps({"type": "text", "text": reply}))


# ── Entry points ──────────────────────────────────────────────────────────────

async def run(port: int = 8000, gemini=None, bridge=None):
    """Start server within existing asyncio loop (called from bot.py post_init)."""
    global _gemini, _bridge
    if gemini is not None:
        _gemini = gemini
    if bridge is not None:
        _bridge = bridge

    import uvicorn
    config = uvicorn.Config(
        app, host="0.0.0.0", port=port,
        log_level="info", access_log=False,
    )
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
    _bridge = PCBridge(cfg.pc_ws_host, cfg.pc_ws_port)

    async def _main():
        await asyncio.gather(
            _bridge.connect_loop(),
            run(port=cfg.miniapp_port),
        )

    asyncio.run(_main())
