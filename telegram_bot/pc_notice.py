"""Когда писать в Telegram «ПК офлайн / снова онлайн».

Раньше бот сообщал о каждом подключении и о каждом разрыве дольше 20 секунд.
А связь ПК с сервером рвётся постоянно: HF обрывает WebSocket раз в ~10
минут, ноутбук засыпает, Space перезапускается при каждом деплое — и каждый
перезапуск начинался с «🖥 ПК онлайн». В чате копилась лента из десятков
онлайн/офлайн в день, за которой терялись настоящие сообщения.

Теперь правило одно: молчим, пока ПК не пропал всерьёз (_OFFLINE_NOTICE_SEC),
и сообщаем о возвращении, только если до этого сообщили о пропаже.
Текущее состояние всегда видно в /status и на бейдже Mini App.
"""

import asyncio
import logging
import os
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

_OFFLINE_NOTICE_SEC = float(os.getenv("PC_OFFLINE_NOTICE_SEC", "600"))


class PCStatusNotifier:
    def __init__(self, notify: Callable[[str], Awaitable[None]],
                 is_connected: Callable[[], bool],
                 offline_after: float = _OFFLINE_NOTICE_SEC):
        self._notify = notify
        self._is_connected = is_connected
        self._offline_after = offline_after
        self._announced_offline = False
        self._pending: asyncio.Task | None = None

    async def on_change(self, online: bool):
        if online:
            if self._pending:
                self._pending.cancel()
                self._pending = None
            if self._announced_offline:
                self._announced_offline = False
                await self._notify("🖥 ПК снова онлайн — можно управлять компьютером.")
            return

        if self._pending:
            return
        self._pending = asyncio.create_task(self._confirm_offline())

    async def _confirm_offline(self):
        try:
            await asyncio.sleep(self._offline_after)
        except asyncio.CancelledError:
            return
        self._pending = None
        if self._is_connected() or self._announced_offline:
            return
        self._announced_offline = True
        minutes = max(1, round(self._offline_after / 60))
        await self._notify(f"🌙 ПК офлайн уже {minutes} мин.")
