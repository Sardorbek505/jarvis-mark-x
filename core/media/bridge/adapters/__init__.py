"""JARVIS Mark X — Провайдер-специфичные адаптеры браузерного моста."""

from core.media.bridge.adapters.base import BaseProviderAdapter
from core.media.bridge.adapters.generic_html5 import GenericHTML5Adapter
from core.media.bridge.adapters.youtube import YouTubeAdapter
from core.media.bridge.adapters.vk import VKVideoAdapter
from core.media.bridge.adapters.kinopoisk import KinopoiskAdapter

__all__ = [
    "BaseProviderAdapter",
    "GenericHTML5Adapter",
    "YouTubeAdapter",
    "VKVideoAdapter",
    "KinopoiskAdapter",
]
