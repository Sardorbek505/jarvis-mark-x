"""JARVIS Mark X — Адаптер YouTube (YouTubeAdapter)."""

from core.media.bridge.adapters.base import BaseProviderAdapter


class YouTubeAdapter(BaseProviderAdapter):
    """Специализированный адаптер для YouTube."""

    def __init__(self):
        super().__init__(provider_name="youtube")
