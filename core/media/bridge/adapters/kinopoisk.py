"""JARVIS Mark X — Адаптер Кинопоиск (KinopoiskAdapter)."""

from core.media.bridge.adapters.base import BaseProviderAdapter


class KinopoiskAdapter(BaseProviderAdapter):
    """Специализированный адаптер для Кинопоиска."""

    def __init__(self):
        super().__init__(provider_name="kinopoisk")
