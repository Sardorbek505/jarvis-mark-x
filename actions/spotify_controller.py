"""
Spotify Controller Integration

Wrapper for new Spotify API controller.
Replaces old UI-based music_player with official API control.
"""

import json
import os
from typing import Dict, Any, Optional
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.spotify import SpotifyController


class SpotifyAPI:
    """
    Spotify API controller wrapper.
    """
    
    def __init__(self):
        self.controller: Optional[SpotifyController] = None
        self._load_credentials()
    
    def _load_credentials(self) -> None:
        """Load Spotify credentials from config."""
        # Get absolute path to config file
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_file = os.path.join(script_dir, "config", "api_keys.json")
        
        if not os.path.exists(config_file):
            print("[SpotifyAPI] Config file not found (Spotify will be disabled)")
            print(f"[SpotifyAPI] Expected config at: {config_file}")
            return
        
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            client_id = config.get('spotify_client_id', '').strip()
            client_secret = config.get('spotify_client_secret', '').strip()
            redirect_uri = config.get('spotify_redirect_uri', 'http://127.0.0.1:8888/callback')
            refresh_token = config.get('spotify_refresh_token', '').strip()
            
            if not client_id or not client_secret:
                print("[SpotifyAPI] Missing Spotify credentials")
                return
            
            # Initialize controller
            self.controller = SpotifyController(client_id, client_secret, redirect_uri)
            
            # Set refresh token if available
            if refresh_token:
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


# Global instance
spotify_api = SpotifyAPI()


def spotify_player(parameters: Dict[str, Any], player=None) -> str:
    """
    Main entry point for Spotify player tool with fallback to system media keys.
    """
    if not spotify_api.is_ready():
        try:
            from actions.music_player import music_player
            return music_player(parameters=parameters, player=player)
        except Exception as e:
            return f"Медиа недоступно: {e}"

    action = (parameters.get("action") or "").strip().lower()
    query = (parameters.get("query") or "").strip()
    value = parameters.get("value")
    
    # Log if player available
    if player:
        player.write_log(f"SYS: 🎵 Spotify API — action: {action}")
    
    # Playback actions
    if action in ("play", "start", "включить", "запустить", "поставь"):
        if query:
            res = spotify_api.play_query(query)
            if not res or any(k in res.lower() for k in ("запускается", "недоступен", "не нашёл", "не нашел", "ошибка", "failed")):
                try:
                    from actions.music_player import music_player
                    return music_player(parameters=parameters, player=player)
                except Exception as exc:
                    return res or "Не удалось запустить воспроизведение, сэр."
            return res
        else:
            res = spotify_api.resume()
            if not res or any(k in res.lower() for k in ("запускается", "недоступен", "ошибка")):
                try:
                    from actions.music_player import music_player
                    return music_player(parameters=parameters, player=player)
                except Exception:
                    return res or "Воспроизведение, сэр."
            return res

    elif action in ("pause", "пауза"):
        res = spotify_api.pause()
        if not res or any(k in res.lower() for k in ("недоступен", "не удалось")):
            try:
                from actions.music_player import music_player
                return music_player(parameters={"action": "pause"}, player=player)
            except Exception:
                return res or "Пауза, сэр."
        return res

    elif action in ("resume", "продолжай", "продолжить", "играй"):
        res = spotify_api.resume()
        if not res or any(k in res.lower() for k in ("недоступен", "не удалось")):
            try:
                from actions.music_player import music_player
                return music_player(parameters={"action": "resume"}, player=player)
            except Exception:
                return res or "Продолжаю, сэр."
        return res

    elif action in ("next", "next_track", "skip", "следующий", "дальше"):
        res = spotify_api.next_track()
        if not res or any(k in res.lower() for k in ("недоступен", "не удалось")):
            try:
                from actions.music_player import music_player
                return music_player(parameters={"action": "next"}, player=player)
            except Exception:
                return res or "Следующий трек, сэр."
        return res

    elif action in ("prev", "previous", "prev_track", "предыдущий", "назад"):
        res = spotify_api.previous_track()
        if not res or any(k in res.lower() for k in ("недоступен", "не удалось")):
            try:
                from actions.music_player import music_player
                return music_player(parameters={"action": "prev"}, player=player)
            except Exception:
                return res or "Предыдущий трек, сэр."
        return res

    elif action in ("stop", "стоп", "остановить", "выключи"):
        res = spotify_api.pause()
        try:
            from actions.music_player import music_player
            return music_player(parameters={"action": "stop"}, player=player)
        except Exception:
            return res or "Музыка остановлена, сэр."
    
    # Volume actions
    elif action in ("volume_up", "louder", "громче"):
        current_info = spotify_api.get_device_info()
        current_vol = current_info.get('volume', 50)
        new_vol = min(100, current_vol + 10)
        return spotify_api.set_volume(new_vol)
    
    elif action in ("volume_down", "quieter", "тише"):
        current_info = spotify_api.get_device_info()
        current_vol = current_info.get('volume', 50)
        new_vol = max(0, current_vol - 10)
        return spotify_api.set_volume(new_vol)
    
    elif action in ("volume", "громкость"):
        if value is not None:
            try:
                vol = int(value)
                return spotify_api.set_volume(vol)
            except ValueError:
                return "Укажите громкость числом, сэр."
        return "Укажите громкость, сэр."
    
    # Playback mode actions
    elif action in ("shuffle", "перемешай", "случайный"):
        return spotify_api.shuffle(True)
    
    elif action in ("repeat", "повтор"):
        if value:
            return spotify_api.repeat(value)
        return spotify_api.repeat('context')
    
    # Info actions
    elif action in ("now_playing", "what_playing", "что играет", "кто поет", "какой трек"):
        return spotify_api.now_playing()
    
    # Mood actions
    elif action in ("mood", "настроение"):
        if query:
            return spotify_api.play_mood(query)
        return "Укажите настроение, сэр."
    
    # Natural language mood detection
    elif "спокойное" in action or "расслаб" in action:
        return spotify_api.play_mood("спокойное")
    
    elif "мотивацион" in action or "энергич" in action:
        return spotify_api.play_mood("мотивационное")
    
    elif "ночной" in action or "night" in action:
        return spotify_api.play_mood("ночной вайб")
    
    elif "работа" in action or "work" in action:
        return spotify_api.play_mood("работа")
    
    elif "трениров" in action or "спорт" in action:
        return spotify_api.play_mood("тренировка")
    
    return f"Не понял команду: «{action}»."
