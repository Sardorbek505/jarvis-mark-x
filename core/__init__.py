"""
Core модули JARVIS
"""
from .news_manager import (
    NewsManager,
    NewsPreferences,
    NewsArticle,
    NewsAggregator,
    NewsFilter,
    NewsSummarizer,
    get_news_digest,
    get_personalized_news,
    add_news_interest,
    add_news_keyword
)

from .translation_manager import (
    TranslationManager,
    TranslationPreferences,
    TranslationHistory,
    ContextMemory,
    translate_text,
    get_translation_history,
    search_translations,
    resolve_language_code,
    set_language_enabled,
    set_default_language,
    set_learning_mode
)

__all__ = [
    # News
    'NewsManager',
    'NewsPreferences',
    'NewsArticle',
    'NewsAggregator',
    'NewsFilter',
    'NewsSummarizer',
    'get_news_digest',
    'get_personalized_news',
    'add_news_interest',
    'add_news_keyword',
    # Translation
    'TranslationManager',
    'TranslationPreferences',
    'TranslationHistory',
    'ContextMemory',
    'translate_text',
    'get_translation_history',
    'search_translations',
    'resolve_language_code',
    'set_language_enabled',
    'set_default_language',
    'set_learning_mode'
]
