"""FastAPI server — serves Mini App static files + WebSocket."""
import logging
import sys
from pathlib import Path

import aiofiles
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from telegram_bot.config import load as load_config
from telegram_bot.server.memory import Memory
from telegram_bot.server.voice_session import VoiceSession

logger = logging.getLogger(__name__)
cfg = load_config()

MINIAPP_DIR = Path(__file__).resolve().parent.parent / "miniapp"

app = FastAPI(title="JARVIS Mini App", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(MINIAPP_DIR)), name="static")


@app.get("/")
async def root():
    return FileResponse(MINIAPP_DIR / "index.html")

@app.get("/{filename}")
async def static_file(filename: str):
    path = MINIAPP_DIR / filename
    if path.exists() and path.is_file():
        return FileResponse(path)
    return HTMLResponse("Not found", status_code=404)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, user_id: int = 0):
    await websocket.accept()

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
        pass
    except Exception as e:
        logger.error(f"Session error (user={user_id}): {e}")
    finally:
        await session.cleanup()


def start():
    import uvicorn
    uvicorn.run(
        "telegram_bot.server.app:app",
        host="0.0.0.0",
        port=int(cfg.miniapp_port),
        log_level="info",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start()
