"""
Утренний брифинг — собирает погоду, календарь и новости в один монолог.
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import json

_BASE = Path(__file__).parent.parent


def morning_briefing(parameters: Dict[str, Any], player=None) -> str:
    """
    Утренний брифинг — погода + календарь + новости в стиле ДЖАРВИС.
    
    Args:
        parameters: параметры (не используются, но нужны для интерфейса)
        player: плеер (не используется)
        
    Returns:
        Строка с брифингом в стиле ДЖАРВИС
    """
    # Загрузка памяти пользователя
    user_data = _load_memory()
    user_name = user_data.get("name", "сэр")
    city = user_data.get("city", "Москва")
    
    # Сбор компонентов брифинга
    weather_part = _get_weather(city)
    calendar_part = _get_calendar()
    news_part = _get_news()
    
    # Сборка финального монолога
    briefing = _assemble_briefing(user_name, weather_part, calendar_part, news_part)
    
    return briefing


def _load_memory() -> Dict[str, Any]:
    """Загружает данные пользователя из памяти."""
    memory_file = _BASE / "memory" / "data.json"
    
    try:
        if memory_file.exists():
            with open(memory_file, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    
    return {"name": "сэр", "city": "Москва"}


def _get_weather(city: str) -> str:
    """Получает погоду для города."""
    try:
        from actions.weather import weather_action
        result = weather_action({"city": city})
        return result
    except Exception as e:
        return f"Данные о погоде временно недоступны, сэр."


def _get_calendar() -> str:
    """Получает события календаря на сегодня."""
    try:
        from actions.calendar import get_events
        events = get_events("today")
        
        if not events:
            return "Расписание чисто."
        
        # Формируем список событий
        event_list = []
        for event in events[:5]:  # Максимум 5 событий
            time_str = event.get("time", "")
            title = event.get("title", "Без названия")
            if time_str:
                event_list.append(f"{time_str} — {title}")
            else:
                event_list.append(title)
        
        return ". ".join(event_list)
    except Exception as e:
        return "Расписание временно недоступно."


def _get_news() -> str:
    """Получает топ-3 новости."""
    try:
        from core.news_manager import NewsManager
        manager = NewsManager()
        articles = manager.fetch_all_news()
        
        if not articles:
            return "Новостная лента недоступна."
        
        # Берём топ-3 по relevance
        top_articles = sorted(articles, key=lambda x: x.get("relevance", 0), reverse=True)[:3]
        
        headlines = []
        for article in top_articles:
            title = article.get("title", "")
            if title:
                headlines.append(title)
        
        if headlines:
            return ". ".join(headlines)
        else:
            return "Новостная лента пуста."
    except Exception as e:
        return "Новостная лента недоступна."


def _assemble_briefing(user_name: str, weather: str, calendar: str, news: str) -> str:
    """Собирает финальный монолог в стиле ДЖАРВИС."""
    
    # Начало
    briefing = f"Доброе утро, {user_name}. "
    
    # Погода
    if weather and "недоступен" not in weather.lower():
        briefing += f"{weather} "
    
    # Календарь
    if calendar and "чисто" not in calendar.lower() and "недоступен" not in calendar.lower():
        briefing += f"По расписанию: {calendar}. "
    elif calendar == "Расписание чисто.":
        briefing += "Расписание на сегодня чисто. "
    
    # Новости
    if news and "недоступен" not in news.lower() and "пуста" not in news.lower():
        briefing += f"Главное: {news}. "
    
    # Завершение
    current_hour = datetime.now().hour
    if current_hour < 12:
        closing = "К работе готов, сэр."
    else:
        closing = "Чем могу помочь, сэр?"
    
    briefing += closing
    
    return briefing
