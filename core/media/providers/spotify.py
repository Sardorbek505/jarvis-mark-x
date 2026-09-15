"""JARVIS Mark X — Провайдер Spotify (SpotifyProvider).

Поиск и открытие треков, альбомов и плейлистов в десктопном Spotify
или в браузере с созданием `SpotifyMediaController`.
"""

import logging
import platform
import time

from core.media.controllers.spotify import SpotifyMediaController
from core.media.models import MediaRequest
from core.media.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger("jarvis-spotify-provider")
_OS = platform.system()


class SpotifyProvider(BaseProvider):
    """Провайдер музыки Spotify."""

    def __init__(self):
        super().__init__(name="spotify")

    def is_available(self) -> bool:
        try:
            from actions.music_player import _is_spotify_installed, _is_spotify_running
            return _is_spotify_installed() or _is_spotify_running()
        except Exception:
            return False

    def search_and_open(self, request: MediaRequest) -> ProviderResult:
        query = request.title or request.raw_query
        playlist_url = request.url

        try:
            from actions.music_player import (
                _https_to_spotify_uri,
                _open_spotify_uri,
                _spotify_search_track_uri,
                _ui_automation_search,
                _focus_spotify_window,
                _send_media_key,
            )

            # 1. Если дан URL плейлиста
            if playlist_url:
                uri = _https_to_spotify_uri(playlist_url)
                if uri and _open_spotify_uri(uri):
                    time.sleep(1.5)
                    _focus_spotify_window()
                    _send_media_key("playpause")
                    return ProviderResult(
                        success=True,
                        url=playlist_url,
                        message="Плейлист Spotify открыт, сэр.",
                        controller=SpotifyMediaController(),
                    )

            # 2. Поиск трека по названию
            if query:
                track_uri = _spotify_search_track_uri(query)
                if track_uri and _open_spotify_uri(track_uri):
                    time.sleep(1.0)
                    _focus_spotify_window()
                    _send_media_key("playpause")
                    return ProviderResult(
                        success=True,
                        url=track_uri,
                        message=f"Включаю «{query}» в Spotify, сэр.",
                        controller=SpotifyMediaController(),
                        provider="spotify",
                    )

                if _ui_automation_search(query):
                    return ProviderResult(
                        success=True,
                        url=None,
                        message=f"Включаю «{query}» в Spotify, сэр.",
                        controller=SpotifyMediaController(),
                        provider="spotify",
                    )

            # 3. Возобновление воспроизведения
            _send_media_key("playpause")
            return ProviderResult(
                success=True,
                url=None,
                message="Воспроизведение Spotify запущено, сэр.",
                controller=SpotifyMediaController(),
                provider="spotify",
            )
        except Exception as e:
            logger.error("Spotify provider error: %s", e)
            return ProviderResult(
                success=False,
                url=None,
                message=f"Ошибка Spotify: {e}",
                controller=None,
            )
