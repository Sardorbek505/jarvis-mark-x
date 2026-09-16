"""Что подтверждать сигналом, а что — словами.

Принцип (как у Алисы): действие, результат которого пользователь видит или
слышит сам — громкость, пауза, следующий трек, запуск фильма, — подтверждается
коротким сигналом, а не фразой. Слова оставляем там, где без них непонятно,
сработало ли: ошибки, уточнения, таймеры, напоминания, заметки, сообщения.
"""

from __future__ import annotations

import re

# Локальные намерения оркестратора, результат которых очевиден без слов.
PERCEIVABLE_INTENTS = frozenset({"play_media", "play_music", "open_app"})

# Голые подтверждения модели после успешного инструмента: «Готово, сэр.» —
# озвучивать нечего, достаточно сигнала.
_BARE_WORDS = frozenset({
    "готово", "сделано", "выполнено", "исполнено", "есть", "принято", "выполняю",
    "включаю", "включил", "включила", "открываю", "открыл", "запускаю", "запустил",
    "поставил", "ставлю", "переключил", "переключаю", "сэр", "конечно", "разумеется",
})
_WORD_SPLIT = re.compile(r"[^\wё]+", re.IGNORECASE)

_FAILURE_MARKERS = ("не удалось", "не могу", "не найд", "недоступ", "ошибк", "не получил", "не отвеча")


def is_bare_confirmation(text: str) -> bool:
    """«Готово, сэр.» и «Готово. Сделано.» — да; «Готово, таймер поставлен» — нет.

    Голым считается ответ, в котором ВСЕ слова — подтверждения (и не длиннее
    четырёх слов): модель после инструмента любит «Готово. Сделано, сэр.».
    """
    words = [w for w in _WORD_SPLIT.split((text or "").lower()) if w]
    if not words or len(words) > 4:
        return False
    return all(w in _BARE_WORDS for w in words) and any(w != "сэр" for w in words)


def looks_like_failure(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _FAILURE_MARKERS)


def should_stay_silent(intent: str | None, success: bool, text: str | None) -> bool:
    """Успешное очевидное действие — только сигнал."""
    if not success or looks_like_failure(text or ""):
        return False
    return (intent or "") in PERCEIVABLE_INTENTS
