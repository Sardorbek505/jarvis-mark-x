"""JARVIS Mark X — Базовый интерфейс медиа-провайдера (BaseProvider).

Разделяет зоны ответственности согласно Архитектурному Правилу #6:
Провайдер отвечает ИСКЛЮЧИТЕЛЬНО за SEARCH, RESOLVE и OPEN.
Управление воспроизведением (PLAY/PAUSE/SEEK) передаётся Контроллеру.
"""

from abc import ABC, abstractmethod
from typing import NamedTuple, Optional

from core.media.controllers.base import BaseMediaController
from core.media.models import MediaRequest


class ProviderResult(NamedTuple):
    """Результат работы провайдера: флаг успеха, открытый URL, сообщение и контроллер."""
    success: bool = True
    url: Optional[str] = None
    message: str = ""
    controller: Optional[BaseMediaController] = None
    window_handle: Optional[int] = None
    page_opened: bool = True
    provider: Optional[str] = None
    status: str = "LAUNCH_STARTED"


class BaseProvider(ABC):
    """Абстрактный класс провайдера медиа-контента."""

    def __init__(self, name: str):
        self.name = name.lower()

    @abstractmethod
    def is_available(self) -> bool:
        """Проверяет доступность провайдера (сеть, API, приложения)."""
        pass

    @abstractmethod
    def search_and_open(self, request: MediaRequest) -> ProviderResult:
        """Ищет контент, подготавливает прямую ссылку и открывает воспроизведение."""
        pass

    def score_candidate(self, candidate_title: str, request: MediaRequest) -> float:
        """Вычисляет релевантность результата с учётом штрафов за трейлеры, обзоры и клипы."""
        title_low = candidate_title.lower()
        score = 100.0

        # Штрафы за неполноценный контент (трейлеры, обзоры, тизеры), если пользователь просил фильм/сериал
        if request.media_type in ("movie", "series"):
            penalties = ("трейлер", "trailer", "тизер", "teaser", "обзор", "review", "реакция", "клип", "отрывок")
            for pen in penalties:
                if pen in title_low and pen not in request.raw_query.lower():
                    score -= 80.0

        # Бонусы за совпадение сезонов и серий
        if request.season and f"{request.season} сезон" in title_low:
            score += 40.0
        if request.episode and f"{request.episode} серия" in title_low:
            score += 40.0

        return score
