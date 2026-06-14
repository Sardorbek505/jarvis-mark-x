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
               "включи", "выключи", "стоп", "пауза", "следующий", "громкость", "музык",
               "поставь", "запусти", "воспроизведи", "играй", "трек", "песн"],
    "weather": ["weather", "погод", "температур"],
    "app": ["open", "открой", "запусти приложение"],
    "search": ["search", "find", "найди", "поищи", "поиск"],
    "window": ["сверни", "свернуть", "minimize", "закрой окно", "закрыть окно",
               "рабочий стол", "show desktop", "разверни", "maximize",
               "snap left", "snap right", "alt tab", "переключи окно",
               "проводник", "диспетчер задач", "task manager"],
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
        # Spotify / Music — spotify_player expects a dict {action, query, value}
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["music"]):
            from actions.spotify_controller import spotify_player
            params = _parse_music(text_lower)
            result = await asyncio.to_thread(spotify_player, params)
            return result or "Выполнено"

        # Weather — weather_action expects {city}
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["weather"]):
            from actions.weather import weather_action
            city = _extract_after(text_lower, ["в ", "in ", "погода "]) or "Ташкент"
            result = await asyncio.to_thread(weather_action, {"city": city})
            return result or "Погода получена"

        # Open app — open_app expects {app_name}
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["app"]):
            from actions.open_app import open_app
            app_name = _extract_after(text_lower, ["открой ", "запусти ", "open ", "закрой ", "close "])
            result = await asyncio.to_thread(open_app, {"app_name": app_name or text})
            return result or "Выполнено"

        # Web search — web_search expects {query}
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["search"]):
            from actions.web_search import web_search
            query = _extract_after(text_lower, ["найди ", "поищи ", "search ", "find ", "найти "]) or text
            result = await asyncio.to_thread(web_search, {"query": query})
            return result or "Поиск запущен"

        # Window control — window_control expects {action, target}
        if any(k in text_lower for k in _PC_COMMAND_KEYWORDS["window"]):
            from actions.window_control import window_control
            params = _parse_window(text_lower)
            result = await asyncio.to_thread(window_control, params)
            return result or "Выполнено"

        return f"Не понял команду: «{text}». Попробуй /help чтобы увидеть что я умею."

    except Exception as e:
        logger.error(f"PC execute error: {e}")
        return f"Ошибка: {e}"


def _parse_music(text_lower: str) -> dict:
    """Turn a natural-language music command into spotify_player parameters."""
    if any(k in text_lower for k in ["stop", "стоп", "выключи", "пауза", "pause"]):
        return {"action": "pause"}
    if any(k in text_lower for k in ["next", "следующий", "skip"]):
        return {"action": "next"}
    if any(k in text_lower for k in ["prev", "предыдущий", "назад"]):
        return {"action": "prev"}
    if any(k in text_lower for k in ["громче", "louder"]):
        return {"action": "volume_up"}
    if any(k in text_lower for k in ["тише", "quieter"]):
        return {"action": "volume_down"}

    match = re.search(r"(?:play|включи|играй|поставь|запусти)\s+(.+)", text_lower)
    query = match.group(1).strip() if match else ""
    # Strip a trailing "музыку"/"music" filler when no real query was given
    if query in ("музыку", "music", "музыка", "трек", "песню"):
        query = ""
    return {"action": "play", "query": query}


def _extract_after(text_lower: str, markers: list) -> str:
    """Return the text following the first matching marker word, else ''."""
    for marker in markers:
        idx = text_lower.find(marker)
        if idx != -1:
            return text_lower[idx + len(marker):].strip()
    return ""


def _parse_window(text_lower: str) -> dict:
    """Map natural-language window command to window_control parameters."""
    if any(k in text_lower for k in ["сверни все", "свернуть все", "minimize all", "win+m"]):
        return {"action": "minimize_all"}
    if any(k in text_lower for k in ["рабочий стол", "show desktop", "win+d"]):
        return {"action": "show_desktop"}
    if any(k in text_lower for k in ["сверни", "свернуть", "minimize"]):
        # Check if there's a target app
        target = _extract_after(text_lower, ["сверни ", "свернуть "])
        if target and target not in ("окно", "window"):
            return {"action": "activate", "target": target}
        return {"action": "minimize"}
    if any(k in text_lower for k in ["разверни", "maximize"]):
        return {"action": "maximize"}
    if any(k in text_lower for k in ["закрой окно", "закрыть окно", "close window"]):
        return {"action": "close"}
    if any(k in text_lower for k in ["alt tab", "переключи окно", "switch"]):
        return {"action": "switch"}
    if any(k in text_lower for k in ["проводник", "explorer"]):
        return {"action": "open_explorer"}
    if any(k in text_lower for k in ["диспетчер задач", "task manager"]):
        return {"action": "task_manager"}
    return {"action": "minimize_all"}


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
