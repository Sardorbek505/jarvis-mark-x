"""
Модуль для умных новостей с персонализацией
"""
import json
import feedparser
import requests
from pathlib import Path
from typing import Dict, List, Any, Optional
import re

# Таймаут на RSS-фид: без него зависший сервер блокирует всё.
_RSS_TIMEOUT = 5  # секунд
_RSS_USER_AGENT = "Mozilla/5.0 (compatible; JARVIS-RU/1.0; +https://github.com)"

_BASE = Path(__file__).parent.parent


class NewsPreferences:
    """Управляет предпочтениями пользователя для новостей."""
    
    def __init__(self):
        self.preferences_file = _BASE / "config" / "news_preferences.json"
        self.preferences = self._load_preferences()
    
    def _load_preferences(self) -> Dict[str, Any]:
        """Загружает предпочтения из файла."""
        if not self.preferences_file.exists():
            return self._get_default_preferences()
        
        try:
            with open(self.preferences_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return self._get_default_preferences()
    
    def _get_default_preferences(self) -> Dict[str, Any]:
        """Возвращает предпочтения по умолчанию."""
        return {
            "topics": ["технологии", "искусственный интеллект", "программирование"],
            "sources": [
                {
                    "name": "Habr",
                    "url": "https://habr.com/ru/rss/articles/?fl=ru",
                    "type": "rss",
                    "enabled": True
                }
            ],
            "keywords": ["AI", "Python", "JavaScript", "стартап"],
            "exclude_keywords": ["политика", "скандал"],
            "digest_schedule": {
                "morning": "08:00",
                "evening": "20:00"
            },
            "max_articles_per_digest": 5,
            "language": "ru"
        }
    
    def save_preferences(self) -> bool:
        """Сохраняет предпочтения в файл."""
        try:
            self.preferences_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.preferences_file, 'w', encoding='utf-8') as f:
                json.dump(self.preferences, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False
    
    def get_topics(self) -> List[str]:
        """Возвращает список интересующих тем."""
        return self.preferences.get("topics", [])
    
    def get_enabled_sources(self) -> List[Dict[str, Any]]:
        """Возвращает список включенных источников."""
        return [s for s in self.preferences.get("sources", []) if s.get("enabled", False)]
    
    def get_keywords(self) -> List[str]:
        """Возвращает ключевые слова."""
        return self.preferences.get("keywords", [])
    
    def get_exclude_keywords(self) -> List[str]:
        """Возвращает слова для исключения."""
        return self.preferences.get("exclude_keywords", [])
    
    def add_topic(self, topic: str) -> bool:
        """Добавляет тему в интересы."""
        if topic not in self.preferences["topics"]:
            self.preferences["topics"].append(topic)
            return self.save_preferences()
        return True
    
    def remove_topic(self, topic: str) -> bool:
        """Удаляет тему из интересов."""
        if topic in self.preferences["topics"]:
            self.preferences["topics"].remove(topic)
            return self.save_preferences()
        return False
    
    def add_keyword(self, keyword: str) -> bool:
        """Добавляет ключевое слово."""
        if keyword not in self.preferences["keywords"]:
            self.preferences["keywords"].append(keyword)
            return self.save_preferences()
        return True
    
    def enable_source(self, source_name: str) -> bool:
        """Включает источник."""
        for source in self.preferences["sources"]:
            if source["name"] == source_name:
                source["enabled"] = True
                return self.save_preferences()
        return False
    
    def disable_source(self, source_name: str) -> bool:
        """Отключает источник."""
        for source in self.preferences["sources"]:
            if source["name"] == source_name:
                source["enabled"] = False
                return self.save_preferences()
        return False


class NewsArticle:
    """Класс для представления новости."""
    
    def __init__(self, title: str, link: str, description: str, published: str, source: str):
        self.title = title
        self.link = link
        self.description = description
        self.published = published
        self.source = source
        self.relevance_score = 0.0
        self.summary = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразует в словарь."""
        return {
            "title": self.title,
            "link": self.link,
            "description": self.description,
            "published": self.published,
            "source": self.source,
            "relevance_score": self.relevance_score,
            "summary": self.summary
        }


class NewsAggregator:
    """Агрегатор новостей из RSS фидов."""
    
    def __init__(self, preferences: NewsPreferences):
        self.preferences = preferences
    
    def fetch_from_source(self, source: Dict[str, Any]) -> List[NewsArticle]:
        """
        Загружает новости из источника.
        
        Args:
            source: Информация об источнике
            
        Returns:
            Список статей
        """
        articles = []
        
        try:
            if source["type"] == "rss":
                # Качаем сами с таймаутом — feedparser.parse() сам по себе
                # не поддерживает timeout и может зависнуть навсегда.
                try:
                    resp = requests.get(
                        source["url"],
                        timeout=_RSS_TIMEOUT,
                        headers={"User-Agent": _RSS_USER_AGENT},
                    )
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.content)
                except requests.RequestException as e:
                    print(f"[News] Timeout/ошибка {source['name']}: {e}")
                    return articles

                for entry in feed.entries[:20]:  # Максимум 20 статей
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    description = entry.get("description", "")
                    published = entry.get("published", "")
                    
                    # Очистка HTML из описания
                    description = self._clean_html(description)
                    
                    article = NewsArticle(title, link, description, published, source["name"])
                    articles.append(article)
        except Exception as e:
            print(f"Error fetching from {source['name']}: {e}")
        
        return articles
    
    def _clean_html(self, text: str) -> str:
        """Удаляет HTML теги из текста."""
        # Простое удаление HTML тегов
        clean = re.sub(r'<[^>]+>', '', text)
        # Удаление лишних пробелов
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean
    
    def fetch_all_news(self) -> List[NewsArticle]:
        """Загружает новости из всех включенных источников."""
        all_articles = []
        sources = self.preferences.get_enabled_sources()
        
        for source in sources:
            articles = self.fetch_from_source(source)
            all_articles.extend(articles)
        
        return all_articles


class NewsFilter:
    """Фильтрует новости на основе предпочтений."""
    
    def __init__(self, preferences: NewsPreferences):
        self.preferences = preferences
    
    def filter_by_relevance(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        """
        Фильтрует новости по релевантности и рассчитывает скор.
        
        Args:
            articles: Список всех статей
            
        Returns:
            Отфильтрованный список с рассчитанными скорами
        """
        topics = self.preferences.get_topics()
        keywords = self.preferences.get_keywords()
        exclude_keywords = self.preferences.get_exclude_keywords()
        
        filtered_articles = []
        
        for article in articles:
            # Проверка на исключённые ключевые слова
            text_to_check = (article.title + " " + article.description).lower()
            
            if any(excl.lower() in text_to_check for excl in exclude_keywords):
                continue
            
            # Расчёт релевантности
            score = 0.0
            
            # Проверка тем
            for topic in topics:
                if topic.lower() in text_to_check:
                    score += 2.0
            
            # Проверка ключевых слов
            for keyword in keywords:
                if keyword.lower() in text_to_check:
                    score += 1.5
            
            article.relevance_score = score
            
            # Оставляем только статьи с положительным скором
            if score > 0:
                filtered_articles.append(article)
        
        # Сортировка по релевантности
        filtered_articles.sort(key=lambda x: x.relevance_score, reverse=True)
        
        return filtered_articles
    
    def get_top_articles(self, articles: List[NewsArticle], limit: int = 5) -> List[NewsArticle]:
        """Возвращает топ-N статей."""
        return articles[:limit]


class NewsSummarizer:
    """AI суммаризатор новостей."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
    
    def summarize_article(self, article: NewsArticle) -> str:
        """
        Суммирует статью на русском.
        
        Args:
            article: Статья для суммирования
            
        Returns:
            Краткое содержание
        """
        # Простая реализация - берем первое предложение описания
        if article.description:
            sentences = article.description.split('.')
            if sentences:
                return sentences[0].strip() + "."
        
        # Если описания нет, возвращаем заголовок
        return article.title
    
    def summarize_digest(self, articles: List[NewsArticle]) -> str:
        """
        Создаёт digest из нескольких статей.
        
        Args:
            articles: Список статей
            
        Returns:
            Текст digest
        """
        if not articles:
            return "Нет интересных новостей."
        
        digest_parts = []
        
        for i, article in enumerate(articles, 1):
            summary = self.summarize_article(article)
            digest_parts.append(f"{i}. {article.title}. {summary}")
        
        return "\n\n".join(digest_parts)


class NewsManager:
    """Главный класс для управления новостями."""
    
    def __init__(self):
        self.preferences = NewsPreferences()
        self.aggregator = NewsAggregator(self.preferences)
        self.filter = NewsFilter(self.preferences)
        self.summarizer = NewsSummarizer()
        self.news_cache_file = _BASE / "config" / "news_cache.json"
    
    def get_personalized_news(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Возвращает персонализированные новости.
        
        Args:
            limit: Максимальное количество статей
            
        Returns:
            Список персонализированных новостей
        """
        # Загружаем новости
        all_articles = self.aggregator.fetch_all_news()
        
        # Фильтруем по релевантности
        relevant_articles = self.filter.filter_by_relevance(all_articles)
        
        # Берём топ-N
        top_articles = self.filter.get_top_articles(relevant_articles, limit)
        
        # Суммируем
        for article in top_articles:
            article.summary = self.summarizer.summarize_article(article)
        
        # Преобразуем в словари
        return [article.to_dict() for article in top_articles]
    
    def get_news_digest(self) -> str:
        """
        Возвращает digest новостей для голосового воспроизведения.
        
        Returns:
            Текст digest
        """
        articles = self.get_personalized_news()
        
        if not articles:
            return "Сэр, нет новых интересных новостей по вашим предпочтениям."
        
        digest = f"Сэр, вот {len(articles)} интересных новостей:\n\n"
        
        for i, article in enumerate(articles, 1):
            digest += f"{i}. {article['title']}. {article['summary']}\n"
        
        return digest
    
    def update_preferences(self, updates: Dict[str, Any]) -> bool:
        """
        Обновляет предпочтения.
        
        Args:
            updates: Словарь с обновлениями
            
        Returns:
            True если успешно
        """
        try:
            for key, value in updates.items():
                if key in self.preferences.preferences:
                    self.preferences.preferences[key] = value
            
            return self.preferences.save_preferences()
        except Exception:
            return False
    
    def add_interest(self, topic: str) -> bool:
        """Добавляет интерес."""
        return self.preferences.add_topic(topic)
    
    def remove_interest(self, topic: str) -> bool:
        """Удаляет интерес."""
        return self.preferences.remove_topic(topic)
    
    def add_keyword(self, keyword: str) -> bool:
        """Добавляет ключевое слово."""
        return self.preferences.add_keyword(keyword)


# ─── Удобные функции ───────────────────────────────────────────────────────────────
def get_news_digest() -> str:
    """
    Возвращает digest новостей.
    
    Returns:
        Текст digest
    """
    manager = NewsManager()
    return manager.get_news_digest()


def get_personalized_news(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Возвращает персонализированные новости.
    
    Args:
        limit: Максимальное количество статей
        
    Returns:
        Список новостей
    """
    manager = NewsManager()
    return manager.get_personalized_news(limit)


def add_news_interest(topic: str) -> bool:
    """
    Добавляет интерес в новости.
    
    Args:
        topic: Тема для добавления
        
    Returns:
        True если успешно
    """
    manager = NewsManager()
    return manager.add_interest(topic)


def add_news_keyword(keyword: str) -> bool:
    """
    Добавляет ключевое слово.
    
    Args:
        keyword: Ключевое слово
        
    Returns:
        True если успешно
    """
    manager = NewsManager()
    return manager.add_keyword(keyword)
