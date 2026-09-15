"""JARVIS Mark X — Фасад браузерного моста (BrowserBridge).

Обеспечивает централизованную маршрутизацию команд к расширению браузера
по exact tab_id с динамо-определением Capabilities.
"""

import logging
from typing import Dict, Optional, Any

from core.media.bridge.adapters import (
    BaseProviderAdapter,
    GenericHTML5Adapter,
    KinopoiskAdapter,
    VKVideoAdapter,
    YouTubeAdapter,
)
from core.media.bridge.server import BrowserBridgeServer, get_bridge_server
from core.media.models import MediaCapabilities

logger = logging.getLogger("jarvis-browser-bridge")


class BrowserBridge:
    """Центральный связующий слой между BrowserMediaController и BrowserBridgeServer."""

    def __init__(self):
        self.server: BrowserBridgeServer = get_bridge_server()
        self._adapters: Dict[str, BaseProviderAdapter] = {
            "youtube": YouTubeAdapter(),
            "vk": VKVideoAdapter(),
            "kinopoisk": KinopoiskAdapter(),
            "generic": GenericHTML5Adapter(),
        }

    def get_adapter(self, provider_name: str = "generic") -> BaseProviderAdapter:
        p = (provider_name or "generic").lower()
        if "youtube" in p or "ютуб" in p:
            return self._adapters["youtube"]
        elif "vk" in p or "вк" in p:
            return self._adapters["vk"]
        elif "kinopoisk" in p or "кинопоиск" in p:
            return self._adapters["kinopoisk"]
        return self._adapters["generic"]

    def is_tab_connected(self, tab_id: Optional[str]) -> bool:
        if tab_id and self.server.get_tab_info(tab_id) is not None:
            return True
        if self.server.is_bridge_connected():
            return True
        return len(self.server.get_all_tabs()) > 0

    def close_tab(self, tab_id: str) -> Dict[str, Any]:
        return self.server.close_tab(tab_id)

    def get_dynamic_capabilities(self, tab_id: Optional[str]) -> MediaCapabilities:
        """Динамически вычисляет возможности контроллера на основе активного подключения."""
        if self.is_tab_connected(tab_id):
            return MediaCapabilities(
                play_pause=True,
                seek_relative=True,
                seek_absolute=True,       # Доступно только через Direct HTML5 Media API!
                seek_percent=True,
                volume_control=True,
                set_player_volume=True,   # Внутриплеерная громкость HTML5 видео
                set_system_volume=True,   # Системная громкость Windows
                mute_control=True,
                fullscreen=True,
                next_track=False,
                previous_track=False,
                state_readback=True,      # Реальное состояние является Source of Truth
                position_readback=True,   # HTML5 currentTime
                duration_readback=True,   # HTML5 duration
            )
        else:
            # Fallback на hotkeys с ограниченными возможностями
            return MediaCapabilities(
                play_pause=True,
                seek_relative=True,
                seek_absolute=False,      # Невозможно через hotkeys в браузере!
                seek_percent=True,
                volume_control=True,
                set_player_volume=False,
                set_system_volume=True,
                mute_control=True,
                fullscreen=True,
                next_track=False,
                previous_track=False,
                state_readback=False,
                position_readback=False,
                duration_readback=False,
            )


_global_bridge: Optional[BrowserBridge] = None


def get_browser_bridge() -> BrowserBridge:
    global _global_bridge
    if _global_bridge is None:
        _global_bridge = BrowserBridge()
    return _global_bridge