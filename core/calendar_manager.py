"""
Calendar Manager — управление локальным календарём (JSON).

Хранит события в config/calendar.json:
- События с датой/временем
- Напоминания
- Recurring события
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import re

import logging

_logger = logging.getLogger(__name__)


# ─── Пути ─────────────────────────────────────────────────────────────────────
def _get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _get_base_dir()
CALENDAR_FILE = BASE_DIR / "config" / "calendar.json"


# ─── Парсинг даты/времени ─────────────────────────────────────────────────────
def _parse_datetime(text: str, reference_date: Optional[datetime] = None) -> Optional[datetime]:
    """
    Парсит дату/время из русского текста.
    
    Примеры:
    - "завтра в 14:00"
    - "завтра утром"
    - "через 30 минут"
    - "завтра"
    - "сегодня в 15:00"
    - "в понедельник в 10:00"
    - "на следующей неделе в среду"
    """
    if reference_date is None:
        reference_date = datetime.now()
    
    text = text.lower().strip()
    now = reference_date
    
    # ── Относительные время ─────────────────────────────────────────────────
    # "через 30 минут", "через 2 часа", "через 1 день"
    relative_match = re.match(r'через\s+(\d+)\s+(минут|час|день|часа|часов|дня|дней)', text)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        
        if 'минут' in unit:
            return now + timedelta(minutes=amount)
        elif 'час' in unit:
            return now + timedelta(hours=amount)
        elif 'день' in unit:
            return now + timedelta(days=amount)
    
    # ── Относительные даты ────────────────────────────────────────────────────
    # "завтра", "послезавтра", "сегодня"
    if 'завтра' in text:
        target_date = now + timedelta(days=1)
    elif 'послезавтра' in text:
        target_date = now + timedelta(days=2)
    elif 'сегодня' in text:
        target_date = now
    elif 'на следующей неделе' in text:
        target_date = now + timedelta(weeks=1)
    else:
        target_date = now
    
    # ── Дни недели ──────────────────────────────────────────────────────────
    days_map = {
        'понедельник': 0,
        'вторник': 1,
        'среда': 2,
        'четверг': 3,
        'пятница': 4,
        'суббота': 5,
        'воскресенье': 6,
    }
    
    for day_name, day_num in days_map.items():
        if day_name in text:
            days_ahead = (day_num - now.weekday() + 7) % 7
            if days_ahead == 0 and 'на следующей неделе' in text:
                days_ahead = 7
            target_date = now + timedelta(days=days_ahead)
            break
    
    # ── Время ────────────────────────────────────────────────────────────────
    # "в 14:00", "в 14:30", "в 9:00", "в 9 утра", "в 15:00"
    time_match = re.search(r'в\s+(\d{1,2}):(\d{2})', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        target_date = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif 'утром' in text:
        target_date = target_date.replace(hour=9, minute=0, second=0, microsecond=0)
    elif 'днём' in text or 'днем' in text:
        target_date = target_date.replace(hour=14, minute=0, second=0, microsecond=0)
    elif 'вечером' in text:
        target_date = target_date.replace(hour=18, minute=0, second=0, microsecond=0)
    else:
        # Если время не указано, используем 9:00 по умолчанию
        target_date = target_date.replace(hour=9, minute=0, second=0, microsecond=0)
    
    return target_date


def _parse_duration(text: str) -> int:
    """
    Парсит длительность в минутах.
    
    Примеры:
    - "на 30 минут"
    - "на 1 час"
    - "на 2 часа"
    - "на 1 час 30 минут"
    """
    text = text.lower()
    total_minutes = 0
    
    # Часы
    hour_match = re.search(r'(\d+)\s*час', text)
    if hour_match:
        total_minutes += int(hour_match.group(1)) * 60
    
    # Минуты
    minute_match = re.search(r'(\d+)\s*минут', text)
    if minute_match:
        total_minutes += int(minute_match.group(1))
    
    # Если ничего не найдено, по умолчанию 60 минут
    if total_minutes == 0:
        total_minutes = 60
    
    return total_minutes


# ─── Управление календарём ───────────────────────────────────────────────────
def _load_calendar() -> Dict[str, Any]:
    """Загружает календарь из JSON файла."""
    try:
        if CALENDAR_FILE.exists():
            with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    
    # Структура по умолчанию
    return {
        "events": [],
        "reminders": [],
        "recurring": []
    }


def _save_calendar(data: Dict[str, Any]) -> bool:
    """Сохраняет календарь в JSON файл."""
    try:
        CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CALENDAR_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def add_event(
    title: str,
    datetime_str: str,
    duration: str = "1 час",
    description: str = "",
    location: str = ""
) -> str:
    """
    Добавляет событие в календарь.
    
    Args:
        title: Название события
        datetime_str: Дата/время (русский текст)
        duration: Длительность
        description: Описание
        location: Место
    
    Returns:
        Сообщение об успехе/ошибке
    """
    try:
        # Парсим дату/время
        event_time = _parse_datetime(datetime_str)
        if not event_time:
            return "Не понял дату и время, сэр. Попробуйте: 'завтра в 14:00'"
        
        # Парсим длительность
        duration_minutes = _parse_duration(duration)
        end_time = event_time + timedelta(minutes=duration_minutes)
        
        # Формируем событие
        event = {
            "id": f"event_{int(event_time.timestamp())}",
            "title": title,
            "start": event_time.isoformat(),
            "end": end_time.isoformat(),
            "duration_minutes": duration_minutes,
            "description": description,
            "location": location,
            "created_at": datetime.now().isoformat()
        }
        
        # Сохраняем
        calendar = _load_calendar()
        calendar["events"].append(event)
        _save_calendar(calendar)
        
        # Форматируем для ответа
        date_str = event_time.strftime("%d.%m.%Y")
        time_str = event_time.strftime("%H:%M")
        
        return f"Добавил событие: {title} на {date_str} в {time_str}, сэр."
    
    except Exception as e:
        return f"Ошибка при добавлении события: {str(e)}"


def get_events(date_range: str = "today") -> str:
    """
    Возвращает события за указанный период.
    
    Args:
        date_range: "today", "tomorrow", "week", "all"
    
    Returns:
        Список событий
    """
    try:
        calendar = _load_calendar()
        events = calendar.get("events", [])
        now = datetime.now()
        
        # Фильтруем по дате
        filtered_events = []
        for event in events:
            event_time = datetime.fromisoformat(event["start"])
            
            if date_range == "today":
                if event_time.date() == now.date():
                    filtered_events.append(event)
            elif date_range == "tomorrow":
                if event_time.date() == (now + timedelta(days=1)).date():
                    filtered_events.append(event)
            elif date_range == "week":
                week_end = now + timedelta(days=7)
                if now.date() <= event_time.date() <= week_end.date():
                    filtered_events.append(event)
            else:  # all
                filtered_events.append(event)
        
        # Сортируем по времени
        filtered_events.sort(key=lambda x: x["start"])
        
        if not filtered_events:
            if date_range == "today":
                return "На сегодня событий нет, сэр."
            elif date_range == "tomorrow":
                return "На завтра событий нет, сэр."
            elif date_range == "week":
                return "На этой неделе событий нет, сэр."
            else:
                return "Событий нет, сэр."
        
        # Формируем ответ
        result = []
        for event in filtered_events:
            event_time = datetime.fromisoformat(event["start"])
            date_str = event_time.strftime("%d.%m.%Y")
            time_str = event_time.strftime("%H:%M")
            title = event["title"]
            result.append(f"{date_str} {time_str} — {title}")
        
        header = {
            "today": "На сегодня:",
            "tomorrow": "На завтра:",
            "week": "На этой неделе:",
            "all": "Все события:"
        }.get(date_range, "События:")
        
        return f"{header}\n" + "\n".join(result)
    
    except Exception as e:
        return f"Ошибка при получении событий: {str(e)}"


def delete_event(title_or_id: str) -> str:
    """
    Удаляет событие по названию или ID.
    
    Args:
        title_or_id: Название или ID события
    
    Returns:
        Сообщение об успехе/ошибке
    """
    try:
        calendar = _load_calendar()
        events = calendar.get("events", [])
        
        # Ищем событие
        found_index = -1
        for i, event in enumerate(events):
            if title_or_id.lower() in event["title"].lower():
                found_index = i
                break
            elif title_or_id == event["id"]:
                found_index = i
                break
        
        if found_index == -1:
            return f"Событие '{title_or_id}' не найдено, сэр."
        
        # Удаляем
        deleted_event = events.pop(found_index)
        calendar["events"] = events
        _save_calendar(calendar)
        
        return f"Удалил событие: {deleted_event['title']}, сэр."
    
    except Exception as e:
        return f"Ошибка при удалении события: {str(e)}"


def update_event(
    title_or_id: str,
    new_datetime: Optional[str] = None,
    new_duration: Optional[str] = None
) -> str:
    """
    Обновляет событие.
    
    Args:
        title_or_id: Название или ID события
        new_datetime: Новое дата/время (опционально)
        new_duration: Новая длительность (опционально)
    
    Returns:
        Сообщение об успехе/ошибке
    """
    try:
        calendar = _load_calendar()
        events = calendar.get("events", [])
        
        # Ищем событие
        found_index = -1
        for i, event in enumerate(events):
            if title_or_id.lower() in event["title"].lower():
                found_index = i
                break
            elif title_or_id == event["id"]:
                found_index = i
                break
        
        if found_index == -1:
            return f"Событие '{title_or_id}' не найдено, сэр."
        
        event = events[found_index]
        
        # Обновляем время
        if new_datetime:
            new_time = _parse_datetime(new_datetime)
            if new_time:
                duration = event.get("duration_minutes", 60)
                event["start"] = new_time.isoformat()
                event["end"] = (new_time + timedelta(minutes=duration)).isoformat()
        
        # Обновляем длительность
        if new_duration:
            duration_minutes = _parse_duration(new_duration)
            start = datetime.fromisoformat(event["start"])
            event["end"] = (start + timedelta(minutes=duration_minutes)).isoformat()
            event["duration_minutes"] = duration_minutes
        
        # Сохраняем
        events[found_index] = event
        calendar["events"] = events
        _save_calendar(calendar)
        
        return f"Обновил событие: {event['title']}, сэр."
    
    except Exception as e:
        return f"Ошибка при обновлении события: {str(e)}"


def add_reminder(
    text: str,
    datetime_str: str
) -> str:
    """
    Добавляет напоминание.
    
    Args:
        text: Текст напоминания
        datetime_str: Когда напомнить
    
    Returns:
        Сообщение об успехе/ошибке
    """
    try:
        # Парсим дату/время
        reminder_time = _parse_datetime(datetime_str)
        if not reminder_time:
            return "Не понял когда напомнить, сэр. Попробуйте: 'завтра в 9:00'"
        
        # Формируем напоминание
        reminder = {
            "id": f"reminder_{int(reminder_time.timestamp())}",
            "text": text,
            "datetime": reminder_time.isoformat(),
            "completed": False,
            "created_at": datetime.now().isoformat()
        }
        
        # Сохраняем
        calendar = _load_calendar()
        calendar["reminders"].append(reminder)
        _save_calendar(calendar)
        
        # Форматируем для ответа
        date_str = reminder_time.strftime("%d.%m.%Y")
        time_str = reminder_time.strftime("%H:%M")
        
        return f"Добавил напоминание: {text} на {date_str} в {time_str}, сэр."
    
    except Exception as e:
        return f"Ошибка при добавлении напоминания: {str(e)}"


def get_upcoming_reminders(minutes_ahead: int = 15) -> List[Dict[str, Any]]:
    """
    Возвращает напоминания, которые сработают в ближайшие X минут.
    
    Args:
        minutes_ahead: Сколько минут вперёд смотреть
    
    Returns:
        Список напоминаний
    """
    try:
        calendar = _load_calendar()
        reminders = calendar.get("reminders", [])
        now = datetime.now()
        cutoff = now + timedelta(minutes=minutes_ahead)
        
        upcoming = []
        for reminder in reminders:
            if reminder.get("completed", False):
                continue
            
            reminder_time = datetime.fromisoformat(reminder["datetime"])
            
            if now <= reminder_time <= cutoff:
                upcoming.append(reminder)
        
        return upcoming
    
    except Exception:
        return []


def get_upcoming_events(minutes_ahead: int = 60) -> List[Dict[str, Any]]:
    """
    Возвращает события, начинающиеся в ближайшие X минут, отсортированные по времени.

    В отличие от get_events(), отдаёт структуру, а не текст для озвучки —
    её потребляет движок умных напоминаний.

    Args:
        minutes_ahead: Сколько минут вперёд смотреть

    Returns:
        Список событий (пустой, если календаря нет или он пуст)
    """
    calendar = _load_calendar()
    now = datetime.now()
    cutoff = now + timedelta(minutes=minutes_ahead)

    upcoming = []
    for event in calendar.get("events", []):
        try:
            start = datetime.fromisoformat(event["start"])
        except (KeyError, TypeError, ValueError) as e:
            # Битое событие не должно уносить с собой остальные
            _logger.warning("Пропускаю событие с некорректным началом (%s): %s", e, event)
            continue

        if now <= start <= cutoff:
            upcoming.append(event)

    upcoming.sort(key=lambda e: e["start"])
    return upcoming


def get_todays_schedule() -> str:
    """
    Возвращает расписание на сегодня (события + напоминания).
    
    Returns:
        Расписание на сегодня
    """
    try:
        calendar = _load_calendar()
        now = datetime.now()
        today = now.date()
        
        # События на сегодня
        events = calendar.get("events", [])
        todays_events = []
        for event in events:
            event_time = datetime.fromisoformat(event["start"])
            if event_time.date() == today:
                todays_events.append(event)
        
        # Напоминания на сегодня
        reminders = calendar.get("reminders", [])
        todays_reminders = []
        for reminder in reminders:
            if reminder.get("completed", False):
                continue
            reminder_time = datetime.fromisoformat(reminder["datetime"])
            if reminder_time.date() == today:
                todays_reminders.append(reminder)
        
        # Сортируем по времени
        todays_events.sort(key=lambda x: x["start"])
        todays_reminders.sort(key=lambda x: x["datetime"])
        
        if not todays_events and not todays_reminders:
            return "На сегодня событий и напоминаний нет, сэр."
        
        result = ["📅 Расписание на сегодня:"]
        
        if todays_events:
            result.append("\n🎯 События:")
            for event in todays_events:
                event_time = datetime.fromisoformat(event["start"])
                time_str = event_time.strftime("%H:%M")
                result.append(f"  {time_str} — {event['title']}")
        
        if todays_reminders:
            result.append("\n🔔 Напоминания:")
            for reminder in todays_reminders:
                reminder_time = datetime.fromisoformat(reminder["datetime"])
                time_str = reminder_time.strftime("%H:%M")
                result.append(f"  {time_str} — {reminder['text']}")
        
        return "\n".join(result)
    
    except Exception as e:
        return f"Ошибка при получении расписания: {str(e)}"
