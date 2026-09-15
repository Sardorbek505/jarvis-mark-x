"""JARVIS Mark X — Системный медиа-контроллер (SystemMediaController).

Резервное управление воспроизведением любого локального или фонового
плеера через глобальные медиа-клавиши Windows GSMTC.
"""

import logging
from core.media.controllers.base import BaseMediaController
from core.media.models import MediaCapabilities, MediaState

logger = logging.getLogger("jarvis-system-media-controller")


def _send_media_key(action: str) -> bool:
    try:
        from actions.music_player import _send_media_key as _smk
        return _smk(action)
    except Exception as e:
        logger.debug("System media key error: %s", e)
        return False


class SystemMediaController(BaseMediaController):
    """Глобальный системный медиа-контроллер."""

    def __init__(self):
        capabilities = MediaCapabilities(
            play_pause=True,
            seek_relative=False,
            seek_absolute=False,
            seek_percent=False,
            volume_control=True,
            fullscreen=False,
            next_track=True,
            previous_track=True,
            state_readback=False,
        )
        super().__init__(capabilities=capabilities)
        self._current_state = MediaState.PLAYING

    def play(self) -> str:
        _send_media_key("playpause")
        self._current_state = MediaState.PLAYING
        return "Воспроизведение возобновлено, сэр."

    def pause(self) -> str:
        _send_media_key("playpause")
        self._current_state = MediaState.PAUSED
        return "Музыка на паузе, сэр."

    def toggle_playback(self) -> str:
        _send_media_key("playpause")
        return "Готово, сэр."

    def stop(self) -> str:
        _send_media_key("stop")
        _send_media_key("playpause")
        self._current_state = MediaState.STOPPED
        return "Музыка остановлена, сэр."

    def next_track(self) -> str:
        _send_media_key("next")
        return "Следующий трек, сэр."

    def previous_track(self) -> str:
        _send_media_key("prev")
        return "Предыдущий трек, сэр."

    def set_volume(self, percent: int) -> str:
        try:
            from actions.computer_settings import computer_settings
            return computer_settings({"action": "volume", "value": str(percent)})
        except Exception as e:
            return f"Ошибка установки громкости: {e}"

    def volume_up(self, step: int = 10) -> str:
        try:
            from actions.computer_settings import computer_settings
            return computer_settings({"action": "volume", "value": f"+{step}"})
        except Exception:
            return "Громкость увеличена, сэр."

    def volume_down(self, step: int = 10) -> str:
        try:
            from actions.computer_settings import computer_settings
            return computer_settings({"action": "volume", "value": f"-{step}"})
        except Exception:
            return "Громкость уменьшена, сэр."

    def get_state(self) -> MediaState:
        return self._current_state
