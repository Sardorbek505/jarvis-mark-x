"""JARVIS Mark X — Универсальный HTML5 адаптер (GenericHTML5Adapter)."""

from core.media.bridge.adapters.base import BaseProviderAdapter


class GenericHTML5Adapter(BaseProviderAdapter):
    """Универсальный адаптер для обычных страниц с тегом <video> или <audio>."""

    def __init__(self):
        super().__init__(provider_name="generic")
