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

# Окно, в течение которого оборвавшийся ПК считается переподключающимся.
# Замер 05.08.2026: сессии живут 4.8-27.7 мин, возврат занимает ~7 с.
_RELINK_GRACE_SEC = 20.0


class PCBridge:
    def __init__(self, *_args, **_kwargs):
        # *_args kept for backward-compatible constructor calls
        self._clients: dict = {}   # client_id -> websocket (FastAPI WebSocket)
        self._pending: dict = {}   # req_id -> Future
        self._notify_cb: Optional[Callable] = None
        self._on_change: Optional[Callable] = None
        self._unlinked_at: Optional[float] = None   # monotonic, когда ушёл последний ПК
        self._linked_evt = asyncio.Event()          # взводится при подключении ПК

    @property
    def connected(self) -> bool:
        """ПК считается на связи и в короткое окно после обрыва.

        Инфраструктура HF рвёт WebSocket без close-фрейма в среднем раз в
        10 минут, клиент возвращается за ~7 секунд. Без этого окна каждый
        обрыв был виден пользователю: команды отвечали «ПК офлайн», а
        `bot.py` вообще переставал распознавать их как команды ПК и
        отправлял «сделай скриншот» в Gemini — тот отвечал болтовнёй."""
        return bool(self._clients) or self._relinking

    @property
    def _relinking(self) -> bool:
        return (self._unlinked_at is not None
                and time.monotonic() - self._unlinked_at < _RELINK_GRACE_SEC)

    async def _await_client(self, timeout: float) -> bool:
        """Ждёт возвращения ПК, но только если он именно переподключается."""
        if self._clients:
            return True
        if not self._relinking:
            return False       # ПК действительно выключен — не томим пользователя
        left = _RELINK_GRACE_SEC - (time.monotonic() - self._unlinked_at)
        try:
            await asyncio.wait_for(self._linked_evt.wait(), min(timeout, max(left, 0.0)))
            return bool(self._clients)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False

    def on_notification(self, callback: Callable):
        self._notify_cb = callback

    def on_status_change(self, callback: Callable):
        """callback(online: bool) — fired when a PC connects/disconnects."""
        self._on_change = callback

    # ── PC connection lifecycle ────────────────────────────────────────────────

    async def register(self, ws) -> int:
        cid = id(ws)
        self._clients[cid] = ws
        self._unlinked_at = None
        self._linked_evt.set()
        logger.info(f"PC linked (total={len(self._clients)})")
        await self._fire_change(True)
        return cid

    async def unregister(self, cid: int):
        self._clients.pop(cid, None)
        if not self._clients:
            self._linked_evt.clear()
            self._unlinked_at = time.monotonic()
        logger.info(f"PC unlinked (total={len(self._clients)})")
        # ВАЖНО: сюда идёт СЫРОЕ состояние, а не `self.connected` с окном
        # переподключения. Иначе подписчик получает «онлайн» на разрыве, не
        # запускает свой отсчёт офлайна — и при реально выключенном ПК
        # уведомление «ПК офлайн» не приходит никогда, а бейдж в Mini App
        # остаётся зелёным. Гашение мигания — задача подписчика (там уже есть
        # дебаунс 20 с), окно здесь нужно только для маршрутизации команд.
        await self._fire_change(bool(self._clients))

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
        if not await self._await_client(timeout):
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
        if not await self._await_client(timeout):
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
