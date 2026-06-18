"""
Server-side PC link registry — runs on Render/VPS (inside the Mini App server).

Home PCs connect OUT to us via the /pc-link WebSocket (works behind NAT).
This class keeps those connections and routes commands to them.
"""
import asyncio
import json
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class PCBridge:
    def __init__(self, *_args, **_kwargs):
        # *_args kept for backward-compatible constructor calls
        self._clients: dict = {}   # client_id -> websocket (FastAPI WebSocket)
        self._pending: dict = {}   # req_id -> Future
        self._notify_cb: Optional[Callable] = None
        self._on_change: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        return len(self._clients) > 0

    def on_notification(self, callback: Callable):
        self._notify_cb = callback

    def on_status_change(self, callback: Callable):
        """callback(online: bool) — fired when a PC connects/disconnects."""
        self._on_change = callback

    # ── PC connection lifecycle ────────────────────────────────────────────────

    async def register(self, ws) -> int:
        cid = id(ws)
        self._clients[cid] = ws
        logger.info(f"PC linked (total={len(self._clients)})")
        await self._fire_change(True)
        return cid

    async def unregister(self, cid: int):
        self._clients.pop(cid, None)
        logger.info(f"PC unlinked (total={len(self._clients)})")
        await self._fire_change(self.connected)

    async def _fire_change(self, online: bool):
        if self._on_change:
            try:
                await self._on_change(online)
            except Exception as e:
                logger.debug(f"status change cb: {e}")

    # ── Incoming messages from the PC ──────────────────────────────────────────

    async def handle_message(self, msg: dict):
        mtype = msg.get("type")
        if mtype == "response":
            fut = self._pending.pop(msg.get("req_id", ""), None)
            if fut and not fut.done():
                fut.set_result({
                    "text": msg.get("text", ""),
                    "image_b64": msg.get("image_b64"),
                })
        elif mtype == "notification" and self._notify_cb:
            await self._notify_cb(msg.get("text", ""), msg.get("user_id"))

    # ── Sending commands to the PC ─────────────────────────────────────────────

    async def _send(self, text: str, user_id: int, timeout: float) -> Optional[dict]:
        if not self._clients:
            return None
        ws = next(iter(self._clients.values()))  # single PC for now
        req_id = f"{user_id}_{int(time.monotonic() * 1000)}"
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        try:
            await ws.send_text(json.dumps({
                "type": "command",
                "text": text,
                "user_id": user_id,
                "req_id": req_id,
            }))
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception as e:
            logger.debug(f"PC send: {e}")
            self._pending.pop(req_id, None)
            return None

    async def send_command(self, text: str, user_id: int, timeout: float = 25.0) -> Optional[str]:
        result = await self._send(text, user_id, timeout)
        return None if result is None else result.get("text", "")

    async def send_command_full(self, text: str, user_id: int, timeout: float = 25.0) -> Optional[dict]:
        return await self._send(text, user_id, timeout)

    async def send_userbot(self, target: str, text: str, as_voice: bool,
                           user_id: int, timeout: float = 30.0) -> Optional[dict]:
        """Ask the home PC's Telethon userbot to deliver an outbound message.
        Returns the PC's {"text": ...} response, or None if no PC / timeout."""
        if not self._clients:
            return None
        ws = next(iter(self._clients.values()))
        req_id = f"ub_{user_id}_{int(time.monotonic() * 1000)}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        try:
            await ws.send_text(json.dumps({
                "type": "command",
                "action": "send_telegram",
                "target": target,
                "text": text,
                "as_voice": as_voice,
                "user_id": user_id,
                "req_id": req_id,
            }))
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception as e:
            logger.debug(f"send_userbot: {e}")
            self._pending.pop(req_id, None)
            return None

    async def connect_loop(self):
        """No-op — bridge is passive (server side). Kept for API compatibility."""
        return
