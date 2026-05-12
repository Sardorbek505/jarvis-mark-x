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
    search_translations
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
    'search_translations'
]
