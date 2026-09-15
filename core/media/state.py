"""JARVIS Mark X — Менеджер состояния активной медиа-сессии (MediaSessionTracker).

Потокобезопасный реестр активного воспроизведения. Позволяет мгновенно
обращаться к текущей сессии без повторного поиска браузера или файлов.
"""

import logging
import threading
from typing import Optional

from core.media.models import MediaSession, MediaState

logger = logging.getLogger("jarvis-media-state")


class MediaSessionTracker:
    """Синглтон управления активной MediaSession."""

    _instance: Optional["MediaSessionTracker"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._active_session: Optional[MediaSession] = None
        self._state_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "MediaSessionTracker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_active_session(self, session: MediaSession):
        """Регистрирует новую активную сессию."""
        with self._state_lock:
            if self._active_session:
                if self._active_session != session:
                    self._active_session.cancelled = True
                if self._active_session.controller:
                    try:
                        if self._active_session != session and hasattr(self._active_session.controller, "close"):
                            self._active_session.controller.close()
                    except Exception as e:
                        logger.debug("Close previous session note: %s", e)

            self._active_session = session
            logger.info("MediaSessionTracker: Зарегистрирована новая сессия -> %s", session.to_summary())

    register_session = set_active_session

    def get_active_session(self) -> Optional[MediaSession]:
        """Возвращает текущую активную сессию (если она не очищена)."""
        with self._state_lock:
            return self._active_session

    def update_session_state(
        self,
        status: Optional[MediaState] = None,
        position_seconds: Optional[float] = None,
        volume: Optional[int] = None,
        muted: Optional[bool] = None,
        fullscreen: Optional[bool] = None,
    ):
        """Обновляет отдельные поля текущей активной сессии."""
        with self._state_lock:
            if not self._active_session:
                return
            if status is not None:
                self._active_session.status = status
            if position_seconds is not None:
                self._active_session.position_seconds = max(0.0, position_seconds)
            if volume is not None:
                self._active_session.volume = max(0, min(100, volume))
            if muted is not None:
                self._active_session.muted = muted
            if fullscreen is not None:
                self._active_session.fullscreen = fullscreen

    def clear_active_session(self):
        """Очищает активную сессию (например при закрытии плеера)."""
        with self._state_lock:
            if self._active_session:
                self._active_session.cancelled = True
                self._active_session.status = MediaState.STOPPED
                logger.info("MediaSessionTracker: Сессия закрыта -> «%s»", self._active_session.title)
                self._active_session = None


def get_media_tracker() -> MediaSessionTracker:
    return MediaSessionTracker.get_instance()
