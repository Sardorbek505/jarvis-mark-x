"""
Интеграции с внешними системами для умных напоминаний.

Поддерживает:
- Spotify (приглушение/пауза перед встречей)
- Кино (предложение приостановить фильм)
"""

from typing import Dict, Optional, Any


# ─── Интеграция с Spotify ─────────────────────────────────────────────────────
class SpotifyIntegration:
    """Интеграция со Spotify для умных напоминаний."""
    
    def __init__(self):
        self.music_player = None
        try:
            from actions.music_player import MusicPlayer
            self.music_player = MusicPlayer()
        except ImportError:
            pass
    
    def is_playing(self) -> bool:
        """Проверяет играет ли музыка."""
        if not self.music_player:
            return False
        # В реальной реализации нужно проверить статус плеера
        return False
    
    def pause_music(self) -> str:
        """
        Приостанавливает музыку.
        
        Returns:
            Сообщение о результате
        """
        if not self.music_player:
            return "Spotify не доступен"
        
        try:
            # Здесь должна быть реальная интеграция с music_player
            # Для MVP используем PowerShell
            import subprocess
            subprocess.run([
                "powershell", "-Command",
                "(New-Object -ComObject WScript.Shell).SendKeys('{MEDIA_PAUSE}')"
            ], capture_output=True)
            return "Музыка приостановлена"
        except Exception:
            return "Не удалось приостановить музыку"
    
    def lower_volume(self, level: int = 30) -> str:
        """
        Уменьшает громкость.
        
        Args:
            level: Уровень громкости (0-100)
            
        Returns:
            Сообщение о результате
        """
        try:
            import subprocess
            # Уменьшаем громкость на 30%
            for _ in range(5):
                subprocess.run([
                    "powershell", "-Command",
                    "(New-Object -ComObject WScript.Shell).SendKeys('{VOLUME_DOWN}')"
                ], capture_output=True)
            return f"Громкость уменьшена"
        except Exception:
            return "Не удалось изменить громкость"
    
    def prepare_for_event(self, minutes_until: int, priority: str) -> Dict[str, Any]:
        """
        Подготавливает Spotify к событию.
        
        Args:
            minutes_until: Минут до события
            priority: Приоритет события
            
        Returns:
            Словарь с действиями
        """
        actions = []
        
        if not self.is_playing():
            return {"actions": [], "message": "Музыка не играет"}
        
        # За 5 минут — пауза для важных событий
        if minutes_until <= 5 and priority != "low":
            result = self.pause_music()
            actions.append({"action": "pause", "result": result})
        
        # За 10 минут — уменьшаем громкость
        elif minutes_until <= 10:
            result = self.lower_volume(30)
            actions.append({"action": "lower_volume", "result": result})
        
        return {
            "actions": actions,
            "message": f"Подготовлено {len(actions)} действий для Spotify"
        }


# ─── Интеграция с Кино ─────────────────────────────────────────────────────────
class MovieIntegration:
    """Интеграция с Кино для умных напоминаний."""
    
    def __init__(self):
        self.movie_player = None
        try:
            from actions.movie_player import MoviePlayer
            self.movie_player = MoviePlayer()
        except ImportError:
            pass
    
    def is_playing(self) -> bool:
        """Проверяет играет ли фильм."""
        if not self.movie_player:
            return False
        # В реальной реализации нужно проверить статус плеера
        return False
    
    def suggest_pause(self) -> str:
        """
        Предлагает приостановить фильм.
        
        Returns:
            Сообщение с предложением
        """
        return "Сэр, у вас скоро встреча. Хотите приостановить фильм?"
    
    def pause_movie(self) -> str:
        """
        Приостанавливает фильм.
        
        Returns:
            Сообщение о результате
        """
        if not self.movie_player:
            return "Кино плеер не доступен"
        
        try:
            import subprocess
            subprocess.run([
                "powershell", "-Command",
                "(New-Object -ComObject WScript.Shell).SendKeys(' ')"
            ], capture_output=True)
            return "Фильм приостановлен"
        except Exception:
            return "Не удалось приостановить фильм"
    
    def prepare_for_event(self, minutes_until: int, priority: str) -> Dict[str, Any]:
        """
        Подготавливает Кино к событию.
        
        Args:
            minutes_until: Минут до события
            priority: Приоритет события
            
        Returns:
            Словарь с действиями
        """
        actions = []
        
        if not self.is_playing():
            return {"actions": [], "message": "Фильм не играет"}
        
        # За 10 минут — предложение приостановить
        if minutes_until <= 10 and priority != "low":
            suggestion = self.suggest_pause()
            actions.append({
                "action": "suggest_pause",
                "message": suggestion,
                "auto_pause": False  # Не авто-пауза, только предложение
            })
        
        # За 5 минут — авто-пауза для критических событий
        if minutes_until <= 5 and priority == "critical":
            result = self.pause_movie()
            actions.append({
                "action": "pause",
                "result": result,
                "auto_pause": True
            })
        
        return {
            "actions": actions,
            "message": f"Подготовлено {len(actions)} действий для Кино"
        }


# ─── Менеджер интеграций ────────────────────────────────────────────────────────
class IntegrationManager:
    """Управляет всеми интеграциями."""
    
    def __init__(self):
        self.spotify = SpotifyIntegration()
        self.movie = MovieIntegration()
    
    def prepare_for_event(self, event_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Подготавливает все системы к событию.
        
        Args:
            event_context: Контекст события
                - minutes_until: Минут до события
                - priority: Приоритет события
                - current_activity: Текущая активность
                
        Returns:
            Словарь с действиями всех систем
        """
        results = {
            "spotify": None,
            "movie": None,
            "total_actions": 0
        }
        
        minutes_until = event_context.get("minutes_until", 0)
        priority = event_context.get("priority", "normal")
        current_activity = event_context.get("current_activity", "")
        
        # Spotify интеграция
        if current_activity == "music":
            spotify_result = self.spotify.prepare_for_event(minutes_until, priority)
            results["spotify"] = spotify_result
            results["total_actions"] += len(spotify_result.get("actions", []))
        
        # Кино интеграция
        if current_activity == "movie":
            movie_result = self.movie.prepare_for_event(minutes_until, priority)
            results["movie"] = movie_result
            results["total_actions"] += len(movie_result.get("actions", []))
        
        return results
    
    def get_suggestion_message(self, event_context: Dict[str, Any]) -> Optional[str]:
        """
        Генерирует сообщение с предложением для пользователя.
        
        Args:
            event_context: Контекст события
            
        Returns:
            Сообщение или None
        """
        minutes_until = event_context.get("minutes_until", 0)
        current_activity = event_context.get("current_activity", "")
        
        if current_activity == "music" and minutes_until <= 5:
            return "Сэр, у вас встреча через 5 минут. Приглушить музыку?"
        
        if current_activity == "movie" and minutes_until <= 10:
            return "Сэр, у вас встреча через 10 минут. Хотите приостановить фильм?"
        
        return None


# ─── Удобный интерфейс ───────────────────────────────────────────────────────────
def prepare_integrations_for_event(event_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Удобная функция для подготовки интеграций к событию.
    
    Args:
        event_context: Контекст события
        
    Returns:
        Результаты подготовок
    """
    manager = IntegrationManager()
    return manager.prepare_for_event(event_context)


def get_integration_suggestion(event_context: Dict[str, Any]) -> Optional[str]:
    """
    Удобная функция для получения предложения от интеграций.
    
    Args:
        event_context: Контекст события
        
    Returns:
        Сообщение или None
    """
    manager = IntegrationManager()
    return manager.get_suggestion_message(event_context)
