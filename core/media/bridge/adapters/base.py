"""JARVIS Mark X — Базовый провайдер-адаптер браузерного моста."""

import logging
from typing import Any, Dict, Optional

from core.media.bridge.server import BrowserBridgeServer, get_bridge_server

logger = logging.getLogger("jarvis-bridge-adapters")


class BaseProviderAdapter:
    """Базовый класс адаптера взаимодействия с HTML5 медиа-элементом на странице."""

    def __init__(self, provider_name: str = "generic"):
        self.provider_name = provider_name
        self.server: BrowserBridgeServer = get_bridge_server()

    def get_state(self, tab_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not tab_id:
            info = self.server.get_all_tabs()
            return info[0].get("state") if info else None
        tab_info = self.server.get_tab_info(tab_id)
        return tab_info.get("state") if tab_info else None

    def play(self, tab_id: Optional[str]) -> Dict[str, Any]:
        return self.server.send_command("play", tab_id=tab_id)

    def pause(self, tab_id: Optional[str]) -> Dict[str, Any]:
        return self.server.send_command("pause", tab_id=tab_id)

    def get_paused(self, tab_id: Optional[str]) -> Optional[bool]:
        st = self.get_state(tab_id)
        return st.get("paused") if st else None

    def get_current_time(self, tab_id: Optional[str]) -> Optional[float]:
        st = self.get_state(tab_id)
        return float(st["currentTime"]) if st and "currentTime" in st else None

    def get_duration(self, tab_id: Optional[str]) -> Optional[float]:
        st = self.get_state(tab_id)
        return float(st["duration"]) if st and "duration" in st else None

    def seek_absolute(self, tab_id: Optional[str], seconds: float) -> Dict[str, Any]:
        return self.server.send_command("seek_absolute", tab_id=tab_id, seconds=seconds)

    def seek_relative(self, tab_id: Optional[str], seconds: float) -> Dict[str, Any]:
        return self.server.send_command("seek_relative", tab_id=tab_id, seconds=seconds)

    def seek_percent(self, tab_id: Optional[str], percent: float) -> Dict[str, Any]:
        return self.server.send_command("seek_percent", tab_id=tab_id, percent=percent)

    def get_volume(self, tab_id: Optional[str]) -> Optional[int]:
        st = self.get_state(tab_id)
        return int(st["volume"]) if st and "volume" in st else None

    def set_player_volume(self, tab_id: Optional[str], percent: int) -> Dict[str, Any]:
        return self.server.send_command("set_volume", tab_id=tab_id, percent=percent)

    def get_muted(self, tab_id: Optional[str]) -> Optional[bool]:
        st = self.get_state(tab_id)
        return st.get("muted") if st else None

    def mute(self, tab_id: Optional[str]) -> Dict[str, Any]:
        return self.server.send_command("mute", tab_id=tab_id)

    def unmute(self, tab_id: Optional[str]) -> Dict[str, Any]:
        return self.server.send_command("unmute", tab_id=tab_id)

    def get_playback_rate(self, tab_id: Optional[str]) -> Optional[float]:
        st = self.get_state(tab_id)
        return float(st["playbackRate"]) if st and "playbackRate" in st else None

    def set_playback_rate(self, tab_id: Optional[str], rate: float) -> Dict[str, Any]:
        return self.server.send_command("set_playback_rate", tab_id=tab_id, rate=rate)
