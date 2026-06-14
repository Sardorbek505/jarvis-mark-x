"""
Desktop JARVIS WebSocket server — accepts commands from Telegram bot.

Run alongside JARVIS:
    python -m telegram_bot.pc_server

Or import and call start_server() from main.py.
"""
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets

logger = logging.getLogger(__name__)

_PC_COMMAND_KEYWORDS = {
    "music": ["play", "stop", "pause", "next", "prev", "volume",
               "включи", "стоп", "пауза", "следующий", "громкость", "музык"],
    "weather": ["weather", "погод", "температур"],
    "app": ["open", "close", "открой", "закрой"],
    "search": ["search", "find", "найди", "поищи"],
}


class PCServer:
    def __init__(self, port: int = 8765):
        self._port = port
        self._clients: set = set()

    async def start(self):
        async with websockets.serve(self._handler, "0.0.0.0", self._port):
            logger.info(f"PC Server started on port {self._port}")
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                pass

    async def notify_all(self, text: str, user_id: int = None):
        """Push a notification to all connected Telegram bots."""
        msg = json.dumps({"type": "notification", "text": text, "user_id": user_id})
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                self._clients.discard(ws)

    async def _handler(self, ws):
        self._clients.add(ws)
        logger.info(f"Bot connected: {ws.remote_address}")
        try:
            async for raw in ws:
                await self._process(ws, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            logger.info(f"Bot disconnected: {ws.remote_address}")

    async def _process(self, ws, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("type") != "command":
            return

        text = msg.get("text", "")
        result = await _execute(text)
        await ws.send(json.dumps({
            "type": "response",
            "req_id": msg.get("req_id"),
            "text": result,
            "user_id": msg.get("user_id"),
        }))


async def _execute(text: str) -> str:
    text_lower = text.lower()

    try:
        # Spotify / Music
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["music"]):
            from actions.spotify_controller import spotify_player
            match = re.search(r"(?:play|включи|играй|поставь)\s+(.+)", text_lower)
            query = match.group(1).strip() if match else ""
            if query:
                result = await asyncio.to_thread(spotify_player, f"play {query}")
            elif any(k in text_lower for k in ["stop", "стоп", "выключи"]):
                result = await asyncio.to_thread(spotify_player, "pause")
            elif any(k in text_lower for k in ["next", "следующий"]):
                result = await asyncio.to_thread(spotify_player, "next")
            else:
                result = await asyncio.to_thread(spotify_player, text)
            return result or "Выполнено"

        # Weather
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["weather"]):
            from actions.weather import weather_action
            result = await asyncio.to_thread(weather_action, text)
            return result or "Погода получена"

        # Open app
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["app"]):
            from actions.open_app import open_app
            result = await asyncio.to_thread(open_app, text)
            return result or "Выполнено"

        # Web search
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["search"]):
            from actions.web_search import web_search
            result = await asyncio.to_thread(web_search, text)
            return result or "Поиск запущен"

        return f"Команда «{text}» получена. JARVIS выполняет..."

    except Exception as e:
        logger.error(f"PC execute error: {e}")
        return f"Ошибка: {e}"


# Singleton for import into main.py
_server: PCServer = None


def get_server(port: int = 8765) -> PCServer:
    global _server
    if _server is None:
        _server = PCServer(port)
    return _server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from telegram_bot.config import load as load_config
    cfg = load_config()
    try:
        asyncio.run(get_server(cfg.pc_ws_port).start())
    except KeyboardInterrupt:
        logger.info("PC Server stopped.")
