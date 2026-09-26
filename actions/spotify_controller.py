"""
Spotify Controller Integration

Wrapper for new Spotify API controller.
Replaces old UI-based music_player with official API control.
"""

import logging
import os
from typing import Dict, Any, Optional
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.spotify import SpotifyController

_logger = logging.getLogger(__name__)


class SpotifyAPI:
    """
    Spotify API controller wrapper.
    """
    
    def __init__(self):
        self.controller: Optional[SpotifyController] = None
        self._load_credentials()
    
    def _load_credentials(self) -> None:
        """Load Spotify credentials from config."""
        # Ключи — оттуда же, откуда их берёт весь Джарвис (%APPDATA%\\JARVIS,
        # env, config рядом с программой). Раньше путь строился от __file__,
        # в .exe это _internal\\config — ключей там нет никогда, и Spotify API
        # в собранной программе не включался ни разу.
        try:
            from core.paths import load_api_keys
            config = load_api_keys()

            client_id = config.get('spotify_client_id', '').strip()
            client_secret = config.get('spotify_client_secret', '').strip()
            redirect_uri = config.get('spotify_redirect_uri', 'http://127.0.0.1:8888/callback')
            refresh_token = config.get('spotify_refresh_token', '').strip()
            
            if not client_id or not client_secret:
                print("[SpotifyAPI] Missing Spotify credentials")
                return
            
            # Initialize controller
            self.controller = SpotifyController(client_id, client_secret, redirect_uri)
            
            # Токен из ключей — только если своего нет: Spotify выдаёт новый
            # refresh-токен при обновлении, и старый из ключей его затирал.
            if refresh_token and not getattr(self.controller.auth, "refresh_token", None):
                self.controller.set_refresh_token(refresh_token)
            
            # Check if ready
            if self.controller.is_ready():
                print("[SpotifyAPI] [OK] Controller ready")
            else:
                print("[SpotifyAPI] [WARN] Controller not authenticated")
                
        except Exception as e:
            print(f"[SpotifyAPI] Failed to load credentials: {e}")
    
    def is_ready(self) -> bool:
        """Check if Spotify API is ready."""
        return self.controller is not None and self.controller.is_ready()
    
    def play_query(self, query: str) -> str:
        """Search and play a track."""
        if not self.is_ready():
            return "Spotify недоступен, сэр. Пожалуйста, настройте credentials."
        
        return self.controller.play_query(query)
    
    def play_context(self, uri: str) -> str:
        """Play a context (album, playlist)."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        if self.controller.play_context(uri):
            return "Открываю плейлист, сэр."
        return "Не удалось открыть плейлист, сэр."
    
    def pause(self) -> str:
        """Pause playback."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.pause()
    
    def resume(self) -> str:
        """Resume playback."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.resume()
    
    def next_track(self) -> str:
        """Skip to next track."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.next_track()
    
    def previous_track(self) -> str:
        """Go to previous track."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.previous_track()
    
    def set_volume(self, percent: int) -> str:
        """Set volume."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.set_volume(percent)
    
    def shuffle(self, state: bool) -> str:
        """Toggle shuffle."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.shuffle(state)
    
    def repeat(self, mode: str) -> str:
        """Set repeat mode."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.repeat(mode)
    
    def now_playing(self) -> str:
        """Get currently playing track."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.now_playing()
    
    def play_mood(self, mood: str) -> str:
        """Play music based on mood."""
        if not self.is_ready():
            return "Spotify недоступен, сэр."
        
        return self.controller.play_mood(mood)
    
    def get_device_info(self) -> Dict[str, Any]:
        """Get current device info."""
        if not self.is_ready():
            return {}
        
        return self.controller.get_device_info()


# Экземпляр создаётся по первому требованию: раньше он поднимался при импорте
# и сразу ходил в сеть обновлять токен — каждый запуск Джарвиса ждал Spotify
# (а в России он без VPN не отвечает до таймаута).
_api: Optional["SpotifyAPI"] = None


def get_spotify_api() -> "SpotifyAPI":
    global _api
    if _api is None:
        _api = SpotifyAPI()
    return _api


def spotify_player(parameters: Dict[str, Any], player=None) -> str:
    """Музыка. Основной путь — actions/music_player: точный трек по ссылке
    spotify:track + медиа-сессия Windows (пауза, дальше, что играет) — он
    работает и на Free, и на Premium.

    Раньше всё шло через Spotify Web API: управление там только для Premium
    (с февраля 2026 — и сами dev-приложения только у Premium-владельца),
    нужно «активное устройство», а откат на запасной путь решался по словам
    в ответе («недоступен», «ошибка»…) — половина случаев в него не попадала.
    """
    from actions.music_player import music_player
    return music_player(parameters=parameters, player=player)
