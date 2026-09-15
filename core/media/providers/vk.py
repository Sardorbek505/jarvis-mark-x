"""JARVIS Mark X — Провайдер VK Видео (VKVideoProvider).

Обеспечивает поиск и запуск фильмов/сериалов на vkvideo.ru:
  - Формирует структурированный поисковый запрос (фильм/сезон/серия).
  - Открывает карточку видео в браузере и нажимает Enter/Space для запуска.
  - Разворачивает плеер в полный экран без координат мыши.
"""

import logging
import time
import urllib.parse

from core.media.controllers.browser import BrowserMediaController
from core.media.models import MediaRequest
from core.media.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger("jarvis-vk-provider")


class VKVideoProvider(BaseProvider):
    """Провайдер поиска и воспроизведения фильмов на VK Видео."""

    def __init__(self):
        super().__init__(name="vkvideo")

    def is_available(self) -> bool:
        return True

    def search_and_open(self, request: MediaRequest) -> ProviderResult:
        query_parts = [request.title]
        if request.season is not None:
            query_parts.append(f"{request.season} сезон")
        if request.episode is not None:
            query_parts.append(f"{request.episode} серия")
        if request.media_type != "video":
            query_parts.append("фильм")

        search_text = " ".join(query_parts).strip()
        encoded = urllib.parse.quote(search_text)
        url = f"https://vkvideo.ru/?q={encoded}&section=search"

        try:
            from actions.browser_control import browser_control
            browser_control({"action": "go_to", "url": url})
            controller = BrowserMediaController(provider_name="vkvideo")
            import uuid
            session_id = uuid.uuid4().hex
            controller.session_id = session_id

            def _finish_vk_launch(ctrl, sid):
                from core.media.state import get_media_tracker
                from core.media.models import MediaState
                tracker = get_media_tracker()

                # 1. Если BrowserBridge подключен — предпочитаем прямое управление через API
                if ctrl._is_bridge_active():
                    for _ in range(15):
                        time.sleep(0.1)
                        active = tracker.get_active_session()
                        if not active or active.session_id != sid or active.cancelled or active.status == MediaState.STOPPED:
                            logger.info("VK launch aborted: session cancelled or stopped")
                            return
                    try:
                        adapter = ctrl._get_adapter()
                        adapter.play(ctrl.tab_id)
                        logger.info("VK playback confirmed via BrowserBridge")
                    except Exception as exc:
                        logger.debug("VK bridge play note: %s", exc)
                    return

                # 2. Безопасный фолбэк по хоткеям: квантованное ожидание с проверкой отмены
                for _ in range(15):
                    time.sleep(0.1)
                    active = tracker.get_active_session()
                    if not active or active.session_id != sid or active.cancelled or active.status == MediaState.STOPPED:
                        logger.info("VK launch aborted: session cancelled or stopped during sleep")
                        return

                active = tracker.get_active_session()
                if not active or active.session_id != sid or active.cancelled or active.status == MediaState.STOPPED:
                    logger.info("VK launch aborted before Enter: session inactive")
                    return

                from core.media.controllers.browser import _attach_desktop
                _attach_desktop()

                hwnd = ctrl._find_and_verify_window()
                if not hwnd:
                    logger.warning("VK launch aborted: target window not found")
                    return

                ctrl.window_handle = hwnd
                if not ctrl._focus_safe():
                    logger.warning("VK launch aborted: could not focus target window")
                    return

                ctrl._send_key_safe("enter")

                for _ in range(8):
                    time.sleep(0.1)
                    active = tracker.get_active_session()
                    if not active or active.session_id != sid or active.cancelled or active.status == MediaState.STOPPED:
                        logger.info("VK launch aborted before fullscreen")
                        return

                ctrl.enter_fullscreen()

            import threading
            threading.Thread(target=_finish_vk_launch, args=(controller, session_id), daemon=True, name="JarvisVKLaunch").start()

            msg = f"Включаю «{request.title}» на VK Видео в полный экран, приятного просмотра, сэр."
            if request.season and request.episode:
                msg = f"Включаю {request.season} сезон {request.episode} серию «{request.title}» на VK Видео, сэр."

            return ProviderResult(
                success=True,
                url=url,
                message=msg,
                controller=controller,
                window_handle=None,
                provider="vk",
                status="LAUNCH_STARTED",
            )
        except Exception as e:
            logger.error("VK Video provider error: %s", e)
            return ProviderResult(
                success=False,
                url=url,
                message=f"Сбой запуска VK Видео: {e}",
                controller=None,
                provider="vk",
                status="PLAYBACK_FAILED",
            )
