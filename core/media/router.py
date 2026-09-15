"""JARVIS Mark X — Маршрутизатор провайдеров (ProviderRouter).

Централизованная конфигурация дефолтных провайдеров:
  MEDIA_PROVIDER_DEFAULTS = {
      "movie": "vk",
      "series": "vk",
      "episode": "vk",
      "music": "spotify",
      "video": "youtube",
  }

Правила роутинга:
  1. ДЕФОЛТ ДЛЯ ФИЛЬМОВ/СЕРИАЛОВ: VK Video (https://vkvideo.ru/) — обязательный дефолт.
  2. ЯВНЫЙ ПРОВАЙДЕР: Перекрывает дефолт, если пользователь сам указал платформу (на Кинопоиске / на YouTube).
  3. БЕЗ SILENT FALLBACK: Для фильмов при отсутствии совпадений на VK Video НЕ происходит автоматического
     перехода на YouTube или Кинопоиск — выдаётся сообщение с отказом.
"""

import logging
from typing import Dict, Optional

from core.media.models import MediaRequest, MediaType
from core.media.providers.base import BaseProvider, ProviderResult
from core.media.providers.kinopoisk import KinopoiskProvider
from core.media.providers.spotify import SpotifyProvider
from core.media.providers.vk import VKVideoProvider
from core.media.providers.youtube import YouTubeProvider

logger = logging.getLogger("jarvis-provider-router")

MEDIA_PROVIDER_DEFAULTS = {
    "movie": "vk",
    "series": "vk",
    "episode": "vk",
    "music": "spotify",
    "video": "youtube",
}


class ProviderRouter:
    """Маршрутизатор провайдеров контента согласно принципам JARVIS Mark X."""

    def __init__(self):
        vk_inst = VKVideoProvider()
        self.providers: Dict[str, BaseProvider] = {
            "vkvideo": vk_inst,
            "vk": vk_inst,
            "youtube": YouTubeProvider(),
            "kinopoisk": KinopoiskProvider(),
            "spotify": SpotifyProvider(),
        }

    def resolve_and_open(self, request: MediaRequest) -> ProviderResult:
        """Подбирает лучший провайдер и открывает контент без неявных фаллбэков для фильмов."""
        target_name = (request.provider or "auto").strip().lower()

        # ── 1. Явный провайдер (пользователь сам указал платформу) ───────────────
        if target_name in self.providers and target_name != "auto":
            provider = self.providers[target_name]
            if provider.is_available():
                logger.info("ProviderRouter: Использование явного провайдера '%s'", provider.name)
                res = provider.search_and_open(request)
                if res.success:
                    return res
                else:
                    return ProviderResult(
                        success=False,
                        url=None,
                        message=f"Не удалось найти «{request.title}» на {provider.name.upper()}, сэр.",
                        controller=None,
                        provider=provider.name,
                    )
            else:
                return ProviderResult(
                    success=False,
                    url=None,
                    message=f"Провайдер {provider.name.upper()} недоступен, сэр.",
                    controller=None,
                    provider=provider.name,
                )

        # ── 2. Дефолтный выбор на основе MEDIA_PROVIDER_DEFAULTS ──────────────────
        request.media_type.value if hasattr(request.media_type, "value") else str(request.media_type)

        # ── 3. Фильмы, сериалы и эпизоды -> STRICT VK VIDEO (Без silent fallback!) ──
        if request.media_type in (MediaType.MOVIE, MediaType.SERIES, MediaType.UNKNOWN, "movie", "series", "episode"):
            vk_provider = self.providers["vkvideo"]
            if vk_provider.is_available():
                logger.info("ProviderRouter: Поиск фильма/сериала «%s» на VK Видео (default)", request.title)
                res = vk_provider.search_and_open(request)
                if res.success:
                    return res

            logger.info("ProviderRouter: Контент «%s» не найден на VK Видео. Silent fallback заблокирован.", request.title)
            return ProviderResult(
                success=False,
                url=None,
                message=f"Не удалось найти фильм «{request.title}» на VK Видео, сэр.",
                controller=None,
                provider="vk",
            )

        # ── 4. Музыка -> Spotify (Fallback на YouTube если Spotify недоступен) ────
        elif request.media_type == MediaType.MUSIC:
            spotify = self.providers["spotify"]
            if spotify.is_available():
                res = spotify.search_and_open(request)
                if res.success:
                    return res
            yt = self.providers["youtube"]
            if yt.is_available():
                return yt.search_and_open(request)

        # ── 5. Обычные видео -> YouTube (Fallback на VK Video) ────────────────────
        elif request.media_type == MediaType.VIDEO:
            yt = self.providers["youtube"]
            if yt.is_available():
                res = yt.search_and_open(request)
                if res.success:
                    return res
            vk = self.providers["vkvideo"]
            if vk.is_available():
                return vk.search_and_open(request)

        return ProviderResult(
            success=False,
            url=None,
            message=f"Не удалось найти «{request.title}», сэр.",
            controller=None,
        )


_global_provider_router: Optional[ProviderRouter] = None


def get_provider_router() -> ProviderRouter:
    global _global_provider_router
    if _global_provider_router is None:
        _global_provider_router = ProviderRouter()
    return _global_provider_router