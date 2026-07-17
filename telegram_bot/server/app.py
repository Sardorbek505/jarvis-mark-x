"""FastAPI server — serves Mini App static files + WebSocket."""
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from telegram_bot.config import load as load_config
from telegram_bot.server.memory import Memory
from telegram_bot.server.voice_session import VoiceSession

logger = logging.getLogger(__name__)
cfg = load_config()

MINIAPP_DIR = Path(__file__).resolve().parent.parent / "miniapp"

app = FastAPI(title="JARVIS Mini App", docs_url=None, redoc_url=None)

# CORS — required for Telegram Mini App WebSocket
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return FileResponse(MINIAPP_DIR / "index.html")


@app.get("/{filename:path}")
async def static_file(filename: str):
    # Resolve and ensure the path stays inside MINIAPP_DIR (no path traversal)
    base = MINIAPP_DIR.resolve()
    try:
        path = (base / filename).resolve()
        path.relative_to(base)
    except (ValueError, OSError):
        return HTMLResponse("Forbidden", status_code=403)
    if path.exists() and path.is_file():
        return FileResponse(path)
    return HTMLResponse("Not found", status_code=404)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, user_id: int = 0):
    await websocket.accept()
    logger.info(f"Mini App connected: user_id={user_id}")

    memory = Memory(user_id)
    await memory.init()

    session = VoiceSession(
        user_id=user_id,
        ws=websocket,
        memory=memory,
        config=cfg,
    )

    try:
        await session.run()
    except WebSocketDisconnect:
        logger.info(f"Mini App disconnected: user_id={user_id}")
    except Exception as e:
        logger.error(f"Session error (user={user_id}): {e}")
    finally:
        await session.cleanup()


def start():
    import uvicorn
    uvicorn.run(
        "telegram_bot.server.app:app",
        host="0.0.0.0",
        port=cfg.miniapp_port,
        log_level="info",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start()
