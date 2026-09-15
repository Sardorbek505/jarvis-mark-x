"""JARVIS Mark X — Адаптер VK Video (VKVideoAdapter)."""

from core.media.bridge.adapters.base import BaseProviderAdapter


class VKVideoAdapter(BaseProviderAdapter):
    """Специализированный адаптер для VK Видео."""

    def __init__(self):
        super().__init__(provider_name="vk")
