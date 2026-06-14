"""WebSocket client — connects VPS bot to Desktop JARVIS."""
import asyncio
import json
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


class PCBridge:
    RECONNECT_INTERVAL = 30

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._ws = None
        self._connected = False
        self._pending: dict = {}
        self._notify_cb: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        return self._connected

    def on_notification(self, callback: Callable):
        self._notify_cb = callback

    async def connect_loop(self):
        """Reconnect loop — runs as background task."""
        if not self._host or not _WS_AVAILABLE:
            if not _WS_AVAILABLE:
                logger.warning("websockets package not installed — PC bridge disabled")
            return

        uri = f"ws://{self._host}:{self._port}"
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20, open_timeout=10) as ws:
                    self._ws = ws
                    self._connected = True
                    logger.info(f"PC bridge connected: {uri}")
                    async for raw in ws:
                        await self._on_message(raw)
            except Exception as e:
                logger.debug(f"PC bridge: {e}")
            finally:
                self._ws = None
                self._connected = False
                # Fail all pending futures
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_result(None)
                self._pending.clear()

            await asyncio.sleep(self.RECONNECT_INTERVAL)

    async def _on_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("type") == "response":
            fut = self._pending.pop(msg.get("req_id", ""), None)
            if fut and not fut.done():
                fut.set_result(msg.get("text", ""))

        elif msg.get("type") == "notification" and self._notify_cb:
            await self._notify_cb(msg.get("text", ""), msg.get("user_id"))

    async def send_command(self, text: str, user_id: int, timeout: float = 10.0) -> Optional[str]:
        if not self._connected or not self._ws:
            return None

        req_id = f"{user_id}_{int(time.monotonic() * 1000)}"
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut

        try:
            await self._ws.send(json.dumps({
                "type": "command",
                "text": text,
                "user_id": user_id,
                "req_id": req_id,
            }))
            return await asyncio.wait_for(fut, timeout=timeout)
        except (asyncio.TimeoutError, Exception) as e:
            logger.debug(f"Bridge send: {e}")
            self._pending.pop(req_id, None)
            return None
