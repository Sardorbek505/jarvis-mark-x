"""
Действие: визуальный анализ экрана (Vision Mode).
Захватывает скриншот → отправляет в Gemini Vision → возвращает обзор.
"""

from actions.vision import vision_action


def vision_review(parameters: dict, player=None) -> str:
    """Главная точка входа для tool 'vision_review', делегирующая в единый модуль actions.vision."""
    if player:
        focus = parameters.get("focus", "general")
        player.write_log(f"SYS: 👁 Анализ экрана ({focus})...")
    return vision_action(parameters)
