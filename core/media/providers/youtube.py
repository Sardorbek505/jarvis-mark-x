"""JARVIS Mark X — Провайдер YouTube (YouTubeProvider).

Обеспечивает поиск и запуск видео, треков, роликов и обзоров на YouTube.
"""

import logging
import re
import time
import urllib.parse
import urllib.request

from core.media.controllers.browser import BrowserMediaController
from core.media.models import MediaRequest
from core.media.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger("jarvis-youtube-provider")


class YouTubeProvider(BaseProvider):
    """Провайдер поиска и воспроизведения на YouTube."""

    def __init__(self):
        super().__init__(name="youtube")

    def is_available(self) -> bool:
        return True

    def _find_direct_video_url(self, query: str) -> str:
        try:
            encoded = urllib.parse.quote(query)
            url = f"https://www.youtube.com/results?search_query={encoded}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "ru,en;q=0.9",
                }
            )
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                if matches:
                    return f"https://www.youtube.com/watch?v={matches[0]}&autoplay=1"
        except Exception as e:
            logger.debug("YouTube direct match note: %s", e)

        return f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"

    def search_and_open(self, request: MediaRequest) -> ProviderResult:
        query_parts = [request.title]
        if request.season:
            query_parts.append(f"{request.season} сезон")
        if request.episode:
            query_parts.append(f"{request.episode} серия")

        search_text = " ".join(query_parts).strip()
        url = self._find_direct_video_url(search_text)

        try:
            from actions.browser_control import browser_control
            browser_control({"action": "go_to", "url": url})
            controller = BrowserMediaController(provider_name="youtube")
            import uuid
            session_id = uuid.uuid4().hex
            controller.session_id = session_id

            def _finish_yt_launch(ctrl, sid):
                from core.media.state import get_media_tracker
                from core.media.models import MediaState
                tracker = get_media_tracker()

                # 1. Если BrowserBridge подключен — предпочитаем прямое управление через API
                if ctrl._is_bridge_active():
                    for _ in range(15):
                        time.sleep(0.1)
                        active = tracker.get_active_session()
                        if not active or active.session_id != sid or active.cancelled or active.status == MediaState.STOPPED:
                            logger.info("YouTube launch aborted: session cancelled or stopped")
                            return
                    try:
                        adapter = ctrl._get_adapter()
                        adapter.play(ctrl.tab_id)
                        logger.info("YouTube playback confirmed via BrowserBridge")
                    except Exception as exc:
                        logger.debug("YouTube bridge play note: %s", exc)
                    return

                # 2. Безопасный фолбэк по хоткеям: квантованное ожидание с проверкой отмены
                for _ in range(15):
                    time.sleep(0.1)
                    active = tracker.get_active_session()
                    if not active or active.session_id != sid or active.cancelled or active.status == MediaState.STOPPED:
                        logger.info("YouTube launch aborted: session cancelled or stopped during sleep")
                        return

                active = tracker.get_active_session()
                if not active or active.session_id != sid or active.cancelled or active.status == MediaState.STOPPED:
                    logger.info("YouTube launch aborted before Enter: session inactive")
                    return

                from core.media.controllers.browser import _attach_desktop
                _attach_desktop()

                hwnd = ctrl._find_and_verify_window()
                if not hwnd:
                    logger.warning("YouTube launch aborted: target window not found")
                    return

                ctrl.window_handle = hwnd
                if not ctrl._focus_safe():
                    logger.warning("YouTube launch aborted: could not focus target window")
                    return

                if "watch?v=" not in url:
                    ctrl._send_key_safe("enter")
                    for _ in range(8):
                        time.sleep(0.1)
                        active = tracker.get_active_session()
                        if not active or active.session_id != sid or active.cancelled or active.status == MediaState.STOPPED:
                            logger.info("YouTube launch aborted before fullscreen")
                            return

                ctrl.enter_fullscreen()

            import threading
            threading.Thread(target=_finish_yt_launch, args=(controller, session_id), daemon=True, name="JarvisYTLaunch").start()

            msg = f"Включаю «{request.title}» на YouTube в полный экран, сэр."
            return ProviderResult(
                success=True,
                url=url,
                message=msg,
                controller=controller,
                window_handle=None,
                provider="youtube",
                status="LAUNCH_STARTED",
            )
        except Exception as e:
            logger.error("YouTube provider error: %s", e)
            return ProviderResult(
                success=False,
                url=url,
                message=f"Не удалось открыть YouTube: {e}",
                controller=None,
                provider="youtube",
                status="PLAYBACK_FAILED",
            )
