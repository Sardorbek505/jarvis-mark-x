"""JARVIS Mark X — Провайдер Кинопоиск (KinopoiskProvider).

Поиск и запуск фильмов на kinopoisk.ru.
"""

import logging
import time
import urllib.parse

from core.media.controllers.browser import BrowserMediaController
from core.media.models import MediaRequest
from core.media.providers.base import BaseProvider, ProviderResult

logger = logging.getLogger("jarvis-kinopoisk-provider")


class KinopoiskProvider(BaseProvider):
    """Провайдер Кинопоиска."""

    def __init__(self):
        super().__init__(name="kinopoisk")

    def is_available(self) -> bool:
        return True

    def search_and_open(self, request: MediaRequest) -> ProviderResult:
        query_parts = [request.title]
        if request.season:
            query_parts.append(f"{request.season} сезон")

        search_text = " ".join(query_parts).strip()
        encoded = urllib.parse.quote(search_text)
        url = f"https://www.kinopoisk.ru/index.php?kp_query={encoded}"

        try:
            from actions.browser_control import browser_control
            browser_control({"action": "go_to", "url": url})
            time.sleep(2.0)

            controller = BrowserMediaController(provider_name="kinopoisk")
            hwnd = controller._find_and_verify_window()
            controller.window_handle = hwnd

            controller._send_key_safe("enter")

            msg = f"Открываю фильм «{request.title}» на Кинопоиске, сэр."
            return ProviderResult(
                success=True,
                url=url,
                message=msg,
                controller=controller,
                window_handle=hwnd,
            )
        except Exception as e:
            logger.error("Kinopoisk provider error: %s", e)
            return ProviderResult(
                success=False,
                url=url,
                message=f"Ошибка Кинопоиска: {e}",
                controller=None,
            )
