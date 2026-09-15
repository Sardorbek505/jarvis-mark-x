"""JARVIS Mark X — Базовый класс медиа-контроллера (BaseMediaController).

Определяет единый интерфейс управления воспроизведением для любых плееров:
браузеров, Spotify, локальных медиа-плееров (VLC) и системных аудиосессий.
"""

from abc import ABC, abstractmethod
from typing import Optional

from core.media.models import MediaCapabilities, MediaState


class BaseMediaController(ABC):
    """Абстрактный контракт контроллера управления воспроизведением."""

    def __init__(self, capabilities: Optional[MediaCapabilities] = None):
        self.capabilities = capabilities or MediaCapabilities()

    @abstractmethod
    def play(self) -> str:
        """Возобновление воспроизведения."""
        pass

    @abstractmethod
    def pause(self) -> str:
        """Пауза воспроизведения."""
        pass

    def toggle_playback(self) -> str:
        """Переключение пауза/воспроизведение."""
        return self.pause()

    @abstractmethod
    def stop(self) -> str:
        """Остановка и сброс позиции воспроизведения без закрытия сессии/вкладки."""
        pass

    def seek_relative(self, seconds: float) -> str:
        """Относительная перемотка вперёд (+) или назад (-) в секундах."""
        return "Перемотка не поддерживается для этого источника."

    def seek_absolute(self, seconds: float) -> str:
        """Переход на конкретную позицию в секундах."""
        return "Переход по таймкоду не поддерживается для этого источника."

    def seek_percent(self, percent: float) -> str:
        """Переход на процент от длины (0.0 .. 1.0 или 0% .. 100%)."""
        return "Перемотка по проценту не поддерживается для этого источника."

    def set_volume(self, percent: int) -> str:
        """Установка уровня громкости (0-100%)."""
        return "Регулировка громкости плеера не поддерживается."

    def set_player_volume(self, percent: int) -> str:
        """Установка внутриплеерной громкости (0-100%)."""
        if self.capabilities.set_player_volume:
            return self.set_volume(percent)
        return "Изменение внутриплеерной громкости не поддерживается для этого источника, сэр."

    def set_system_volume(self, percent: int) -> str:
        """Установка системной громкости Windows (0-100%)."""
        try:
            from actions.computer_settings import computer_settings
            return computer_settings({"action": "volume", "value": str(percent)})
        except Exception as e:
            return f"Ошибка системной громкости: {e}"

    def volume_up(self, step: int = 10) -> str:
        """Увеличить громкость."""
        return self.set_volume(50)

    def volume_down(self, step: int = 10) -> str:
        """Уменьшить громкость."""
        return self.set_volume(30)

    def mute(self) -> str:
        """Выключить звук."""
        return "Выключение звука не поддерживается."

    def unmute(self) -> str:
        """Включить звук."""
        return "Включение звука не поддерживается."

    def toggle_mute(self) -> str:
        """Переключить мьют."""
        return self.mute()

    def enter_fullscreen(self) -> str:
        """Включить полноэкранный режим."""
        return "Полноэкранный режим не поддерживается."

    def exit_fullscreen(self) -> str:
        """Выйти из полного экрана."""
        return "Полноэкранный режим не поддерживается."

    def toggle_fullscreen(self) -> str:
        """Переключить полноэкранный режим."""
        return self.enter_fullscreen()

    def next_track(self) -> str:
        """Следующий трек."""
        return "Переключение треков не поддерживается."

    def previous_track(self) -> str:
        """Предыдущий трек."""
        return "Переключение треков не поддерживается."

    def get_position(self) -> Optional[float]:
        """Возвращает текущую позицию в секундах (если поддерживается)."""
        return None

    def get_duration(self) -> Optional[float]:
        """Возвращает общую длительность в секундах (если поддерживается)."""
        return None

    def get_remaining_time(self) -> Optional[float]:
        """Возвращает оставшееся время в секундах (если поддерживается)."""
        pos = self.get_position()
        dur = self.get_duration()
        if pos is not None and dur is not None and dur >= pos:
            return dur - pos
        return None

    def get_current_media(self) -> Optional[str]:
        """Возвращает информацию о текущем воспроизведении."""
        return None

    def get_volume(self) -> Optional[int]:
        """Возвращает текущую громкость в % (если поддерживается)."""
        return None

    def get_state(self) -> MediaState:
        """Возвращает состояние воспроизведения."""
        return MediaState.IDLE

    def close(self) -> str:
        """Закрытие медиа (закрытие вкладки/процесса) и очистка сессии."""
        return "Медиа закрыто, сэр."