"""Контракт tool-адаптеров медиасистемы для Gemini Live.

`music_player` и `movie_player` объявлены как `-> str`: Gemini ждёт от них
текст, который можно озвучить. Оркестратор же сигнализирует «не нашёл» и
«провайдер недоступен» исключениями `MediaException`, и они пролетали наружу
мимо контракта. В `main._execute_tool` их ловил общий `except Exception` и
отдавал модели техническую строку «Ошибка инструмента 'music_player': …»
плюс `speak_error` — вместо готового человеческого ответа, который у
`MediaException.message` уже есть («Медиа-контент «Queen» не найден, сэр.»).
"""

from __future__ import annotations

import functools
import logging
from typing import Callable, TypeVar

from core.media.models import MediaException

logger = logging.getLogger("jarvis-media")

F = TypeVar("F", bound=Callable[..., str])


def media_tool(func: F) -> F:
    """Возвращает сообщение `MediaException` вместо проброса исключения."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> str:
        try:
            return func(*args, **kwargs)
        except MediaException as exc:
            logger.info("%s: %s (%s)", func.__name__, exc.message, exc.error_code)
            return exc.message

    return wrapper  # type: ignore[return-value]
