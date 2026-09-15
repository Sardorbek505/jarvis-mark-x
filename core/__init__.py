"""
Core модули JARVIS.

Импорты здесь ленивые (PEP 562). Раньше пакет при любом обращении —
`from core.conversation_state import ...`, `from core.paths import ...` —
затягивал `news_manager` и `translation_manager`, а с ними `feedparser`,
`requests` и `google.genai`. Это десятки секунд на каждый запуск процесса:
на старте Джарвиса, в каждом тесте, в каждом служебном скрипте.

Публичная поверхность не изменилась: `from core import NewsManager` работает
как раньше, но тяжёлый модуль подгружается только в момент первого обращения.
"""

from importlib import import_module
from typing import Any

# Имя -> модуль, в котором оно определено.
_LAZY_EXPORTS = {
    # News
    "NewsManager": "core.news_manager",
    "NewsPreferences": "core.news_manager",
    "NewsArticle": "core.news_manager",
    "NewsAggregator": "core.news_manager",
    "NewsFilter": "core.news_manager",
    "NewsSummarizer": "core.news_manager",
    "get_news_digest": "core.news_manager",
    "get_personalized_news": "core.news_manager",
    "add_news_interest": "core.news_manager",
    "add_news_keyword": "core.news_manager",
    # Translation
    "TranslationManager": "core.translation_manager",
    "TranslationPreferences": "core.translation_manager",
    "TranslationHistory": "core.translation_manager",
    "ContextMemory": "core.translation_manager",
    "translate_text": "core.translation_manager",
    "get_translation_history": "core.translation_manager",
    "search_translations": "core.translation_manager",
    "resolve_language_code": "core.translation_manager",
    "set_language_enabled": "core.translation_manager",
    "set_default_language": "core.translation_manager",
    "set_learning_mode": "core.translation_manager",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module 'core' has no attribute '{name}'")
    value = getattr(import_module(module_path), name)
    globals()[name] = value  # кэшируем, чтобы второй доступ шёл напрямую
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
