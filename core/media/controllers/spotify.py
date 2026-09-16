"""JARVIS Mark X — Контроллер Spotify (SpotifyMediaController).

Управление воспроизведением треков, плейлистов и навигацией в Spotify
через медиа-клавиши Windows, URI schemes и Spotify API.
"""

import logging
import platform
import subprocess

from core.media.controllers.base import BaseMediaController
from core.media.models import MediaCapabilities, MediaState

logger = logging.getLogger("jarvis-spotify-controller")
_OS = platform.system()


# Web API управления плеером требует Premium: после первого 403 не пробуем.
_api_player_forbidden = False


def _spotify_api():
    """Web API Spotify с OAuth (actions/spotify_controller), если авторизован; иначе None.

    Media-клавиша play/pause — переключатель: не зная состояния плеера, ею
    можно случайно ВКЛЮЧИТЬ музыку вместо паузы. Web API детерминирован.
    """
    if _api_player_forbidden:
        return None
    try:
        from actions.spotify_controller import spotify_api
        ctrl = getattr(spotify_api, "controller", None)
        if ctrl is not None and ctrl.is_ready():
            return spotify_api
    except Exception as e:
        logger.debug("Spotify Web API недоступен: %s", e)
    return None


def _system_session_control(action: str) -> bool:
    """Адресная команда сессии Spotify через Windows (без API, без Premium)."""
    try:
        from core.media_session_manager import control_app
        return control_app("spotify", action)
    except Exception as e:
        logger.debug("GSMTC Spotify %s: %s", action, e)
        return False


def _api_result_ok(text: str) -> bool:
    """Ответ контроллера Web API — успех, а не «не удалось/недоступен»."""
    global _api_player_forbidden
    low = (text or "").lower()
    ok = bool(low) and not any(w in low for w in ("не удалось", "недоступен", "нет активного"))
    if not ok:
        _api_player_forbidden = True  # 403 Premium или нет устройства — дальше без API
    return ok


def _send_media_key(action: str) -> bool:
    try:
        from actions.music_player import _send_media_key as _smk
        return _smk(action)
    except Exception as e:
        logger.debug("Send media key note: %s", e)
        return False


class SpotifyMediaController(BaseMediaController):
    """Контроллер управления воспроизведением Spotify."""

    def __init__(self):
        capabilities = MediaCapabilities(
            play_pause=True,
            seek_relative=False,
            seek_absolute=False,
            seek_percent=False,
            volume_control=True,
            set_player_volume=False, # Spotify desktop без API-кредов использует Windows EndpointVolume
            set_system_volume=True,
            fullscreen=False,
            next_track=True,
            previous_track=True,
            state_readback=True,
        )
        super().__init__(capabilities=capabilities)
        self._current_state = MediaState.PLAYING

    def play(self) -> str:
        # Порядок: системная сессия Spotify (адресно, без Premium) →
        # Web API (если отвечает) → media-клавиша (переключатель, последний шанс)
        if _system_session_control("play"):
            self._current_state = MediaState.PLAYING
            return "Продолжаю воспроизведение в Spotify, сэр."
        api = _spotify_api()
        if api is not None:
            try:
                if _api_result_ok(api.controller.resume()):
                    self._current_state = MediaState.PLAYING
                    return "Продолжаю воспроизведение в Spotify, сэр."
            except Exception as e:
                logger.debug("Spotify resume via API note: %s", e)
        _send_media_key("playpause")
        self._current_state = MediaState.PLAYING
        return "Продолжаю воспроизведение в Spotify, сэр."

    def pause(self) -> str:
        if _system_session_control("pause"):
            self._current_state = MediaState.PAUSED
            return "Пауза в Spotify поставлена, сэр."
        api = _spotify_api()
        if api is not None:
            try:
                if _api_result_ok(api.controller.pause()):
                    self._current_state = MediaState.PAUSED
                    return "Пауза в Spotify поставлена, сэр."
            except Exception as e:
                logger.debug("Spotify pause via API note: %s", e)
        _send_media_key("playpause")
        self._current_state = MediaState.PAUSED
        return "Пауза в Spotify поставлена, сэр."

    def toggle_playback(self) -> str:
        _send_media_key("playpause")
        self._current_state = MediaState.PAUSED if self._current_state == MediaState.PLAYING else MediaState.PLAYING
        return "Готово, сэр."

    def stop(self) -> str:
        """Остановка воспроизведения трека БЕЗ завершения процесса приложения Spotify."""
        _send_media_key("stop")
        _send_media_key("playpause")
        self._current_state = MediaState.STOPPED
        return "Музыка в Spotify остановлена, сэр."

    def on_superseded(self) -> str:
        """Новый контент вместо музыки: пауза, а не завершение процесса."""
        if self._current_state == MediaState.PAUSED:
            return "Музыка на паузе, сэр."
        return self.pause()

    def close(self) -> str:
        """Явное закрытие приложения Spotify (завершение процесса)."""
        self.stop()
        if _OS == "Windows":
            try:
                subprocess.run(["taskkill", "/F", "/IM", "Spotify.exe"], capture_output=True, timeout=3)
                return "Приложение Spotify закрыто, сэр."
            except Exception as e:
                logger.debug("Spotify taskkill note: %s", e)
        return "Закрываю Spotify, сэр."

    def next_track(self) -> str:
        if _send_media_key("next"):
            return "Следующий трек, сэр."
        return "Не удалось переключить трек, сэр."

    def previous_track(self) -> str:
        if _send_media_key("prev"):
            return "Предыдущий трек, сэр."
        return "Не удалось переключить трек, сэр."

    def set_player_volume(self, percent: int) -> str:
        return "Изменение внутриплеерной громкости Spotify не поддерживается в режиме Windows EndpointVolume, изменяется системная громкость, сэр."

    def set_system_volume(self, percent: int) -> str:
        try:
            from actions.computer_settings import computer_settings
            return computer_settings({"action": "volume", "value": str(percent)})
        except Exception as e:
            return f"Ошибка системной громкости: {e}"

    def set_volume(self, percent: int) -> str:
        return self.set_system_volume(percent)

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