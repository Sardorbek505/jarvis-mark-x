"""JARVIS Mark X — Главный медиа-оркестратор (MediaOrchestrator).

Выступает единой точкой входа для приложения, Gemini Live Tool Calls и пользовательских команд.
Оркестрирует работу ProviderRouter и MediaSessionTracker.
"""

import logging
from typing import Any, Dict, Optional

from core.media.controllers.base import BaseMediaController
from core.media.controllers.system_media import SystemMediaController
from core.media.models import (
    MediaCapabilities,
    MediaNotFound,
    MediaRequest,
    MediaSession,
    MediaState,
)
from core.media.router import ProviderRouter, get_provider_router
from core.media.state import MediaSessionTracker, get_media_tracker

logger = logging.getLogger("jarvis-media-orchestrator")


class MediaOrchestrator:
    """Единый оркестратор медиа-системы JARVIS Mark X."""

    _instance: Optional["MediaOrchestrator"] = None

    def __init__(self):
        self.router: ProviderRouter = get_provider_router()
        self.tracker: MediaSessionTracker = get_media_tracker()

    @classmethod
    def get_instance(cls) -> "MediaOrchestrator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 1. Воспроизведение контента (Запуск сессии) ─────────────────────────
    def play_media(self, raw_query: str, parameters: Optional[Dict[str, Any]] = None) -> str:
        """Главный метод запуска любого медиа-контента."""
        req = MediaRequest.from_query(raw_query, parameters)
        logger.info("MediaOrchestrator: Обработка запроса %s (type=%s, provider=%s)", req.title, req.media_type, req.provider)

        self.preempt_active_session()
        res = self.router.resolve_and_open(req)
        if res.success:
            import uuid
            session_id = getattr(res.controller, "session_id", None) or uuid.uuid4().hex
            session = MediaSession(
                session_id=session_id,
                media_type=req.media_type,
                title=req.title,
                provider=res.provider or req.provider,
                status=MediaState.PLAYING,
                source_url=res.url,
                controller=res.controller,
                capabilities=res.controller.capabilities if res.controller else MediaCapabilities(),
            )
            self.tracker.register_session(session)
            return res.message
        else:
            raise MediaNotFound(req.title)

    def register_app_session(self, media_type, title: str, provider: str, controller: BaseMediaController) -> MediaSession:
        """Сессия плеера-приложения (Spotify через Web API), запущенного мимо роутера.

        Без регистрации переключение «музыка → фильм» не знает, что глушить.
        """
        import uuid
        session = MediaSession(
            session_id=uuid.uuid4().hex,
            media_type=media_type,
            title=title,
            provider=provider,
            status=MediaState.PLAYING,
            source_url=None,
            controller=controller,
            capabilities=controller.capabilities,
        )
        self.tracker.register_session(session)
        return session

    def preempt_active_session(self) -> None:
        """Глушит играющий контент ДО поиска нового.

        Поиск и открытие фильма занимают секунды; раньше предыдущая сессия
        закрывалась только при регистрации новой, и всё это время играли оба.
        Пауза — мгновенная (Web API Spotify / мост / клавиша), закрытие
        вкладки или процесса по-прежнему делает tracker при смене сессии.
        """
        session = self.tracker.get_active_session()
        if not session or session.cancelled or session.status in (MediaState.STOPPED, MediaState.PAUSED):
            return
        if not session.controller:
            return
        try:
            session.controller.pause()
            self.tracker.update_session_state(status=MediaState.PAUSED)
            logger.info("MediaOrchestrator: «%s» (%s) поставлен на паузу перед новым контентом",
                        session.title, session.provider)
        except Exception as e:
            logger.warning("MediaOrchestrator: не удалось приглушить «%s»: %s", session.title, e)

    # ── 2. Получение активного контроллера ──────────────────────────────────
    def _get_active_controller(self) -> BaseMediaController:
        """Возвращает контроллер активной сессии или системный фолбэк."""
        session = self.tracker.get_active_session()
        if session and session.controller:
            return session.controller
        return SystemMediaController()

    # ── 3. Команды управления воспроизведением ───────────────────────────────
    def play(self) -> str:
        session = self.tracker.get_active_session()
        if session and session.controller:
            res = session.controller.play()
            self.tracker.update_session_state(status=MediaState.PLAYING)
            return res
        return SystemMediaController().play()

    def pause(self) -> str:
        session = self.tracker.get_active_session()
        if session and session.controller:
            res = session.controller.pause()
            self.tracker.update_session_state(status=MediaState.PAUSED)
            return res
        return SystemMediaController().pause()

    def toggle_playback(self) -> str:
        session = self.tracker.get_active_session()
        if session and session.controller:
            return session.controller.toggle_playback()
        return SystemMediaController().toggle_playback()

    def stop(self) -> str:
        """Остановка воспроизведения и сброс на 00:00 БЕЗ очистки активной сессии."""
        self.tracker.drop_suspended_session()
        session = self.tracker.get_active_session()
        if session:
            session.cancelled = True
            session.status = MediaState.STOPPED
            if session.controller:
                res = session.controller.stop()
                self.tracker.update_session_state(status=MediaState.STOPPED)
                return res
        return SystemMediaController().stop()

    def close(self) -> str:
        """Закрытие активной вкладки/процесса плеера И очистка медиа-сессии."""
        session = self.tracker.get_active_session()
        if session:
            session.cancelled = True
            session.status = MediaState.STOPPED
            if session.controller:
                if hasattr(session.controller, "close"):
                    res = session.controller.close()
                else:
                    res = session.controller.stop()
            else:
                res = "Медиа-сессия закрыта, сэр."
            self.tracker.clear_active_session()
            return res
        return SystemMediaController().stop()

    def seek_relative(self, seconds: float) -> str:
        session = self.tracker.get_active_session()
        if not session or not session.controller:
            return "Нет активного фильма для перемотки, сэр."
        if not session.capabilities.seek_relative:
            return "Перемотка не поддерживается для текущего источника, сэр."
        return session.controller.seek_relative(seconds)

    def seek_absolute(self, seconds: float) -> str:
        session = self.tracker.get_active_session()
        if not session or not session.controller:
            return "Нет активного фильма для перемотки, сэр."
        if not session.capabilities.seek_absolute:
            return "Переход по точному времени не поддерживается для этого веб-плеера, сэр."
        return session.controller.seek_absolute(seconds)

    def seek_percent(self, percent: float) -> str:
        session = self.tracker.get_active_session()
        if not session or not session.controller:
            return "Нет активного фильма для перемотки, сэр."
        if not session.capabilities.seek_percent:
            return "Перемотка по проценту не поддерживается для этого источника, сэр."
        return session.controller.seek_percent(percent)

    def set_volume(self, level: int) -> str:
        session = self.tracker.get_active_session()
        controller = session.controller if session else SystemMediaController()
        res = controller.set_volume(level)
        if session:
            self.tracker.update_session_state(volume=level)
        return res

    def set_player_volume(self, level: int) -> str:
        session = self.tracker.get_active_session()
        if session and session.controller:
            if hasattr(session.controller, "set_player_volume"):
                res = session.controller.set_player_volume(level)
            else:
                res = session.controller.set_volume(level)
            self.tracker.update_session_state(volume=level)
            return res
        return "Нет активного плеера для изменения внутриплеерной громкости, сэр."

    def set_system_volume(self, level: int) -> str:
        session = self.tracker.get_active_session()
        if session and session.controller and hasattr(session.controller, "set_system_volume"):
            return session.controller.set_system_volume(level)
        try:
            from actions.computer_settings import computer_settings
            return computer_settings({"action": "volume", "value": str(level)})
        except Exception as e:
            return f"Ошибка настройки системной громкости: {e}"

    def volume_up(self, step: int = 10) -> str:
        session = self.tracker.get_active_session()
        controller = session.controller if session else SystemMediaController()
        return controller.volume_up(step)

    def volume_down(self, step: int = 10) -> str:
        session = self.tracker.get_active_session()
        controller = session.controller if session else SystemMediaController()
        return controller.volume_down(step)

    def enter_fullscreen(self) -> str:
        session = self.tracker.get_active_session()
        if not session or not session.controller:
            return "Нет активного плеера для полного экрана, сэр."
        return session.controller.enter_fullscreen()

    def exit_fullscreen(self) -> str:
        session = self.tracker.get_active_session()
        if not session or not session.controller:
            return "Нет активного плеера, сэр."
        return session.controller.exit_fullscreen()

    def toggle_fullscreen(self) -> str:
        session = self.tracker.get_active_session()
        if not session or not session.controller:
            return "Нет активного плеера, сэр."
        return session.controller.toggle_fullscreen()

    def next_track(self) -> str:
        session = self.tracker.get_active_session()
        controller = session.controller if session else SystemMediaController()
        return controller.next_track()

    def previous_track(self) -> str:
        session = self.tracker.get_active_session()
        controller = session.controller if session else SystemMediaController()
        return controller.previous_track()

    def resume(self) -> str:
        return self.play()

    def mute(self) -> str:
        session = self.tracker.get_active_session()
        if session and session.controller:
            res = session.controller.mute()
            self.tracker.update_session_state(muted=True)
            return res
        return SystemMediaController().mute()

    def unmute(self) -> str:
        session = self.tracker.get_active_session()
        if session and session.controller:
            res = session.controller.unmute()
            self.tracker.update_session_state(muted=False)
            return res
        return SystemMediaController().unmute()

    def toggle_mute(self) -> str:
        session = self.tracker.get_active_session()
        if session and session.controller:
            return session.controller.toggle_mute()
        return SystemMediaController().toggle_mute()

    def get_position(self) -> Optional[float]:
        session = self.tracker.get_active_session()
        if session and session.controller and session.capabilities.position_readback:
            return session.controller.get_position()
        return None

    def get_duration(self) -> Optional[float]:
        session = self.tracker.get_active_session()
        if session and session.controller and session.capabilities.duration_readback:
            return session.controller.get_duration()
        return None

    def get_remaining_time(self) -> Optional[float]:
        session = self.tracker.get_active_session()
        if session and session.controller and session.capabilities.duration_readback and session.capabilities.position_readback:
            return session.controller.get_remaining_time()
        return None

    def get_current_media(self) -> str:
        session = self.tracker.get_active_session()
        if not session:
            return "Ничего не воспроизводится, сэр."
        return session.to_summary()

    def get_status_summary(self) -> str:
        session = self.tracker.get_active_session()
        if not session:
            return "Ничего не воспроизводится, сэр."
        return f"Сейчас воспроизводится: {session.to_summary()}"


def get_media_orchestrator() -> MediaOrchestrator:
    return MediaOrchestrator.get_instance()