"""
Spotify Controller Module

Main controller for Spotify integration.
Combines auth, search, devices, and moods into unified interface.
"""

from typing import Optional, Dict, Any
import requests

from .auth import SpotifyAuth
from .search import SpotifySearch
from .devices import SpotifyDevices
from .moods import SpotifyMoods

import logging
import os
import re
import subprocess

_logger = logging.getLogger(__name__)


def _run_spotify_cli(args: list) -> Optional[str]:
    """Выполняет команду через локальный spotify_cli.exe, если он установлен."""
    cli_paths = [
        os.path.expandvars(r"%APPDATA%\Spotify\spotify_cli.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Spotify\spotify_cli.exe"),
    ]
    cli = next((p for p in cli_paths if os.path.exists(p)), None)
    if not cli:
        return None
    try:
        res = subprocess.run([cli] + args, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception as exc:
        _logger.debug("spotify_cli error: %s", exc)
    return None


_NO_DEVICE_MSG = (
    "Spotify запускается на ПК. "
    "Подождите несколько секунд и повторите команду."
)


class SpotifyController:
    """
    Main Spotify controller with full API control.
    """
    
    API_BASE = "https://api.spotify.com/v1"
    
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.auth = SpotifyAuth(client_id, client_secret, redirect_uri)
        self.search: Optional[SpotifySearch] = None
        self.devices: Optional[SpotifyDevices] = None
        self.moods: Optional[SpotifyMoods] = None
        
        # Track cache for offline mode
        self.track_cache: Dict[str, str] = {}
        self.max_cache_size = 50
        
        # Playback context
        self.last_query = ""
        self.last_uri = ""
        self.last_device = ""
        self.last_volume = 50
        
        # Spotify availability flag
        self._available = True
        self._unavailable_message_shown = False
    
    def is_available(self) -> bool:
        """Check if Spotify API is available (not disabled due to errors)."""
        return self._available
    
    def _disable_spotify(self, reason: str) -> None:
        """Disable Spotify and show message once."""
        self._available = False
        if not self._unavailable_message_shown:
            print(f"[SpotifyController] ⚠️ Spotify отключен: {reason}")
            print("[SpotifyController] 📋 Для восстановления:")
            print("    1. Проверьте credentials в config/api_keys.json")
            print("    2. Получите новый refresh token если нужно")
            print("    3. Перезапустите JARVIS")
            self._unavailable_message_shown = True
    
    def _refresh_components(self) -> bool:
        """Refresh API components with new access token."""
        access_token = self.auth.get_access_token()
        if not access_token:
            return False
        
        self.search = SpotifySearch(access_token, self.auth)
        self.devices = SpotifyDevices(access_token)
        self.moods = SpotifyMoods(access_token)
        return True
    
    def _request_with_refresh(self, method: str, url: str, **kwargs) -> Optional[requests.Response]:
        """
        Make API request with automatic token refresh on 401.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            url: API endpoint URL
            **kwargs: Additional arguments for requests
            
        Returns:
            Response object or None if failed
        """
        if not self._available:
            return None
            
        headers = kwargs.get('headers', {})
        headers['Authorization'] = f'Bearer {self.auth.get_access_token()}'
        kwargs['headers'] = headers
        
        try:
            response = requests.request(method, url, **kwargs)
            
            # If 401 Unauthorized, try refresh once then disable if still failing
            if response.status_code == 401:
                print("[SpotifyController] 401 Unauthorized - attempting token refresh...")
                if self.auth.refresh_access_token():
                    # Retry with new token
                    headers['Authorization'] = f'Bearer {self.auth.get_access_token()}'
                    kwargs['headers'] = headers
                    response = requests.request(method, url, **kwargs)
                    
                    # If still 401 after refresh, disable Spotify
                    if response.status_code == 401:
                        self._disable_spotify("Токен невалидный даже после refresh")
                        return None
                    else:
                        print("[SpotifyController] Token refreshed successfully")
                else:
                    self._disable_spotify("Не удалось обновить токен")
                    return None
            
            return response
            
        except Exception as e:
            print(f"[SpotifyController] Request failed: {e}")
            return None
    
    def _api_request(
        self,
        endpoint: str,
        method: str = 'GET',
        data: Dict = None
    ) -> Optional[Dict]:
        """Make authenticated API request with auto-refresh."""
        if not self._refresh_components():
            return None
        
        try:
            if method == 'GET':
                response = requests.get(
                    f"{self.API_BASE}{endpoint}",
                    headers=self.devices.headers
                )
            elif method == 'PUT':
                response = requests.put(
                    f"{self.API_BASE}{endpoint}",
                    headers=self.devices.headers,
                    json=data
                )
            elif method == 'POST':
                response = requests.post(
                    f"{self.API_BASE}{endpoint}",
                    headers=self.devices.headers,
                    json=data
                )

            response.raise_for_status()
            # 204 No Content (or empty body on 200/202) is a success response
            if response.status_code == 204 or not response.content or not response.text.strip():
                return {}
            try:
                return response.json()
            except Exception:
                return {}
        except Exception as e:
            print(f"[SpotifyController] API request failed: {e}")
            return None
    
    def authenticate(self, auth_code: str) -> bool:
        """Authenticate with authorization code."""
        return self.auth.authenticate(auth_code)
    
    def set_refresh_token(self, refresh_token: str) -> None:
        """Set refresh token directly."""
        self.auth.set_refresh_token(refresh_token)
    
    def is_ready(self) -> bool:
        """Check if controller is ready to use."""
        return self._available and self.auth.is_authenticated()
    
    def ensure_device(self) -> Optional[Dict[str, Any]]:
        """Ensure there's an active device."""
        if not self._refresh_components():
            return None
        
        device = self.devices.ensure_active_device()
        if device:
            self.last_device = device['id']
        
        return device
    
    def play_track(self, uri: str) -> bool:
        """
        Play specific track.
        
        Args:
            uri: Spotify URI (spotify:track:xxxxx)
            
        Returns:
            True if successful
        """
        # 1. Попытка через локальный spotify_cli (мгновенно, локальный IPC)
        cli_out = _run_spotify_cli(["play", uri])
        if cli_out is not None:
            import time
            time.sleep(0.3)
            return True

        # 2. Через Spotify Web API
        device = self.ensure_device()
        if not device:
            # 3. Fallback: запуск через протокол spotify:
            try:
                os.startfile(uri)
                return True
            except Exception:
                return False
        
        # Play track — append device_id as query param to avoid 404
        device_id = device.get('id', '') if device else ''
        endpoint = f'/me/player/play?device_id={device_id}' if device_id else '/me/player/play'
        data = {'uris': [uri]}
        result = self._api_request(endpoint, method='PUT', data=data)

        if result is not None:  # {} on 204 No Content = success
            import time
            time.sleep(0.5)
            return True

        # Если API не сработало, запускаем через протокол
        try:
            os.startfile(uri)
            return True
        except Exception:
            return False

    def play_context(self, uri: str) -> bool:
        """
        Play context (album, playlist, artist).

        Args:
            uri: Spotify URI

        Returns:
            True if successful
        """
        # 1. Попытка через локальный spotify_cli
        cli_out = _run_spotify_cli(["play", uri])
        if cli_out is not None:
            import time
            time.sleep(0.3)
            return True

        # 2. Через Spotify Web API
        device = self.ensure_device()
        if not device:
            try:
                os.startfile(uri)
                return True
            except Exception:
                return False

        # Play context — append device_id as query param to avoid 404
        device_id = device.get('id', '') if device else ''
        endpoint = f'/me/player/play?device_id={device_id}' if device_id else '/me/player/play'
        data = {'context_uri': uri}
        result = self._api_request(endpoint, method='PUT', data=data)

        if result is not None:
            import time
            time.sleep(0.5)
            return True

        try:
            os.startfile(uri)
            return True
        except Exception:
            return False
    
    def play_query(self, query: str) -> str:
        """
        Search and play a track by query.
        First searches in user playlists, then in general tracks.
        
        Args:
            query: Search query
            
        Returns:
            Response message
        """
        query = (query or "").strip()

        # ── Если запрос пустой ("включи музыку", "поставь песню") ───────────────
        if not query:
            # 1. Пробуем продолжить текущий трек
            res = self.resume()
            if res and not any(k in res.lower() for k in ("запускается", "недоступен", "не удалось")):
                return res
            # 2. Если ничего не стояло на паузе — включаем топ-плейлист
            top_playlist = "spotify:playlist:61fB55pH3MSaVFU4z51AWy"
            if self.play_context(top_playlist):
                return "Включаю вашу любимую музыку в Spotify, сэр."
            return "Включаю музыку, сэр."

        if not self._refresh_components():
            # Если API недоступно, но CLI работает — ищем через CLI
            cli_res = _run_spotify_cli(["search", query])
            if cli_res:
                m = re.search(r"(spotify:track:[a-zA-Z0-9]+)", cli_res)
                if m and self.play_track(m.group(1)):
                    return f"Включил {query} в Spotify, сэр."
            return "Spotify недоступен, сэр."
        
        # Check cache first
        if query in self.track_cache:
            uri = self.track_cache[query]
            if self.play_track(uri):
                self.last_query = query
                self.last_uri = uri
                return f"Включил {query}, сэр."
        
        # First try to find in user playlists
        if self.search:
            # Search in user's playlists with fuzzy matching
            user_playlists = self.search.get_user_playlists(limit=50)
            if user_playlists:
                # Try fuzzy match against playlist names
                from rapidfuzz import fuzz
                best_playlist = None
                best_score = 0
                
                for playlist in user_playlists:
                    name = playlist.get('name', '')
                    score = fuzz.partial_ratio(query.lower(), name.lower())
                    if score > best_score and score > 60:  # 60% threshold
                        best_score = score
                        best_playlist = playlist
                
                if best_playlist:
                    uri = best_playlist['uri']
                    if self.play_context(uri):
                        self.last_query = query
                        self.last_uri = uri
                        return f"Открываю плейлист '{best_playlist['name']}', сэр."
            
        # Search for track
        if not self.search:
            return "Spotify недоступен, сэр."

        track = self.search.search_track(query)
        if not track:
            # Трека нет — только теперь пробуем чужие плейлисты.
            if self.search:
                playlists = self.search.search_playlists(query, limit=5)
                if playlists:
                    uri = playlists[0]['uri']
                    if self.play_context(uri):
                        self.last_query = query
                        self.last_uri = uri
                        return f"Открываю плейлист '{playlists[0]['name']}', сэр."
            return "Не нашёл этот трек или плейлист, сэр. Попробуйте уточнить название, сэр."
        
        uri = track['uri']
        
        # Cache the result
        self._cache_track(query, uri)
        
        # Play the track
        if self.play_track(uri):
            self.last_query = query
            self.last_uri = uri
            return f"Включил {query}, сэр."

        return _NO_DEVICE_MSG
    
    def pause(self) -> str:
        """Pause playback."""
        cli_out = _run_spotify_cli(["pause"])
        if cli_out is not None:
            return "Пауза, сэр."

        if not self._refresh_components():
            return "Spotify недоступен, сэр."
        
        result = self._api_request('/me/player/pause', method='PUT')
        if result is not None:
            return "Пауза, сэр."
        
        return "Не удалось поставить на паузу, сэр."
    
    def resume(self) -> str:
        """Resume playback."""
        cli_out = _run_spotify_cli(["resume"])
        if cli_out is not None:
            return "Продолжаю, сэр."

        if not self._refresh_components():
            return "Spotify недоступен, сэр."

        device = self.ensure_device()
        if not device:
            return _NO_DEVICE_MSG

        device_id = device.get('id', '')
        endpoint = f'/me/player/play?device_id={device_id}' if device_id else '/me/player/play'
        result = self._api_request(endpoint, method='PUT')

        if result is not None:
            return "Продолжаю, сэр."

        return _NO_DEVICE_MSG
    
    def next_track(self) -> str:
        """Skip to next track."""
        cli_out = _run_spotify_cli(["next"])
        if cli_out is not None:
            return "Следующий трек, сэр."

        if not self._refresh_components():
            return "Spotify недоступен, сэр."
        
        result = self._api_request('/me/player/next', method='POST')
        if result is not None:
            return "Следующий трек, сэр."
        
        return "Не удалось переключить, сэр."
    
    def previous_track(self) -> str:
        """Go to previous track."""
        cli_out = _run_spotify_cli(["previous"])
        if cli_out is not None:
            return "Предыдущий трек, сэр."

        if not self._refresh_components():
            return "Spotify недоступен, сэр."
        
        result = self._api_request('/me/player/previous', method='POST')
        if result is not None:
            return "Предыдущий трек, сэр."
        
        return "Не удалось переключить, сэр."
    
    def set_volume(self, percent: int) -> str:
        """
        Set volume.
        
        Args:
            percent: Volume percentage (0-100)
            
        Returns:
            Response message
        """
        percent = max(0, min(100, percent))

        if not self._refresh_components():
            return "Spotify недоступен, сэр."

        device = self.ensure_device()
        if not device:
            return _NO_DEVICE_MSG
        
        data = {'volume_percent': percent}
        result = self._api_request('/me/player/volume', method='PUT', data=data)
        
        if result is not None:
            self.last_volume = percent
            return f"Громкость {percent}%, сэр."
        
        return "Не удалось изменить громкость, сэр."
    
    def shuffle(self, state: bool) -> str:
        """
        Toggle shuffle.
        
        Args:
            state: True to enable, False to disable
            
        Returns:
            Response message
        """
        if not self._refresh_components():
            return "Spotify недоступен, сэр."
        
        data = {'state': state}
        result = self._api_request('/me/player/shuffle', method='PUT', data=data)
        
        if result is not None:
            return "Перемешивание включено, сэр." if state else "Перемешивание выключено, сэр."
        
        return "Не удалось переключить перемешивание, сэр."
    
    def repeat(self, mode: str) -> str:
        """
        Set repeat mode.
        
        Args:
            mode: 'track', 'context', or 'off'
            
        Returns:
            Response message
        """
        if not self._refresh_components():
            return "Spotify недоступен, сэр."
        
        data = {'state': mode}
        result = self._api_request('/me/player/repeat', method='PUT', data=data)
        
        if result is not None:
            mode_names = {'track': 'трек', 'context': 'контекст', 'off': 'выкл'}
            return f"Повтор {mode_names.get(mode, mode)}, сэр."
        
        return "Не удалось переключить повтор, сэр."
    
    def now_playing(self) -> str:
        """Get currently playing track info."""
        cli_out = _run_spotify_cli(["now-playing"])
        if cli_out:
            lines = [line.strip() for line in cli_out.splitlines() if line.strip()]
            if lines and "Status: Playing" in cli_out:
                track_line = lines[0].split("spotify:")[0].strip()
                return f"Сейчас играет: {track_line}, сэр."
            elif lines and "Status: Paused" in cli_out:
                track_line = lines[0].split("spotify:")[0].strip()
                return f"Сейчас на паузе: {track_line}, сэр."

        if not self._refresh_components():
            return "Spotify недоступен, сэр."
        
        result = self._api_request('/me/player/currently-playing')
        
        if result and 'item' in result:
            track = result['item']
            name = track.get('name', 'Unknown')
            artists = ', '.join(a.get('name', 'Unknown') for a in track.get('artists', []))
            return f"Сейчас играет: {artists} - {name}, сэр."
        
        # Also check if player is playing (not paused)
        result = self._api_request('/me/player')
        if result and result.get('is_playing', False):
            if result.get('item'):
                track = result['item']
                name = track.get('name', 'Unknown')
                artists = ', '.join(a.get('name', 'Unknown') for a in track.get('artists', []))
                return f"Сейчас играет: {artists} - {name}, сэр."
            else:
                return "Музыка на паузе, сэр."
        
        return "Ничего не играет, сэр."
    
    def play_mood(self, mood: str) -> str:
        """
        Play music based on mood.
        First checks if mood matches user playlists, then uses moods system.
        
        Args:
            mood: Mood description or playlist name
            
        Returns:
            Response message
        """
        if not self._refresh_components():
            return "Spotify недоступен, сэр."
        
        # First try to find in user playlists with fuzzy matching
        if self.search:
            user_playlists = self.search.get_user_playlists(limit=50)
            if user_playlists:
                from rapidfuzz import fuzz
                best_playlist = None
                best_score = 0
                
                for playlist in user_playlists:
                    name = playlist.get('name', '')
                    score = fuzz.partial_ratio(mood.lower(), name.lower())
                    if score > best_score and score > 60:  # 60% threshold
                        best_score = score
                        best_playlist = playlist
                
                if best_playlist:
                    uri = best_playlist['uri']
                    if self.play_context(uri):
                        self.last_query = mood
                        self.last_uri = uri
                        return f"Открываю ваш плейлист '{best_playlist['name']}', сэр."
        
        # Fall back to moods system
        if not self.moods:
            return "Spotify недоступен, сэр."
        
        uri = self.moods.get_mood_playlist_uri(mood)
        if not uri:
            return f"Не нашёл плейлист для настроения: {mood}, сэр. Попробуйте указать название вашего плейлиста, сэр."
        
        if self.play_track(uri):
            return f"Включаю {mood}, сэр."
        
        return "Не удалось включить, сэр."
    
    def _cache_track(self, query: str, uri: str) -> None:
        """Cache track URI for offline mode."""
        if len(self.track_cache) >= self.max_cache_size:
            # Remove oldest entry (FIFO)
            oldest_key = next(iter(self.track_cache))
            del self.track_cache[oldest_key]
        
        self.track_cache[query] = uri
    
    def get_device_info(self) -> Dict[str, Any]:
        """Get current device info."""
        if not self._refresh_components():
            return {}
        
        return self.devices.get_device_info()
