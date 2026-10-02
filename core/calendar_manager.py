"""
Calendar Manager — управление локальным календарём (JSON).

Хранит события в config/calendar.json:
- События с датой/временем
- Напоминания
- Recurring события
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import re
import threading
import time

from core.storage import atomic_write_json, load_json_or_quarantine

import logging

_logger = logging.getLogger(__name__)


# ─── Пути ─────────────────────────────────────────────────────────────────────
def _get_base_dir() -> Path:
    from core.paths import get_data_root   # см. там: .exe пишет в %APPDATA%
    return get_data_root()


BASE_DIR = _get_base_dir()
CALENDAR_FILE = BASE_DIR / "config" / "calendar.json"


# ─── Парсинг даты/времени ─────────────────────────────────────────────────────
_NUM_WORDS = {
    "ноль": 0, "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
}
_NUM = r"\d+|(?:(?:" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")\s*)+"

# Основы дней недели: «в пятницу», «в среду», «в воскресенье» — падеж не важен.
_WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3,
             "пятниц": 4, "суббот": 5, "воскресень": 6}


def _number(token: str) -> Optional[int]:
    """«25», «двадцать пять» → 25."""
    token = token.strip()
    if token.isdigit():
        return int(token)
    words = token.split()
    if not words or any(w not in _NUM_WORDS for w in words):
        return None
    return sum(_NUM_WORDS[w] for w in words)


def _relative(text: str, now: datetime) -> Optional[datetime]:
    """«через 10 минут», «через десять минут», «через час», «через полчаса»,
    «через полтора часа», «через 2 дня»."""
    if not text.startswith("через"):
        return None
    rest = text[len("через"):].strip()
    if rest.startswith("полчаса"):
        return now + timedelta(minutes=30)
    if rest.startswith("полтора час"):
        return now + timedelta(minutes=90)
    m = re.match(rf"({_NUM})?\s*(минут|мин|час|день|дня|дней|недел)", rest)
    if not m:
        return None
    amount = _number(m.group(1)) if m.group(1) else 1
    if amount is None:
        return None
    unit = m.group(2)
    if unit.startswith("мин"):
        return now + timedelta(minutes=amount)
    if unit == "час":
        return now + timedelta(hours=amount)
    if unit == "недел":
        return now + timedelta(weeks=amount)
    return now + timedelta(days=amount)


def _clock_time(text: str) -> Optional[tuple]:
    """Час и минута: «в 14:00», «в 9.30», «в 7 вечера», «в 12 ночи», «в 16»."""
    m = re.search(r"(?:^|\s)(?:в\s+)?(\d{1,2})[:.](\d{2})(?:\s+(утра|дня|вечера|ночи))?", text)
    if not m:
        m = re.search(r"(?:^|\s)в\s+(\d{1,2})()(?:\s*час\w*)?(?:\s+(утра|дня|вечера|ночи))?(?!\S)", text)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    part = m.group(3)
    if part in ("дня", "вечера") and hour < 12:
        hour += 12
    elif part == "ночи" and hour == 12:
        hour = 0
    elif part == "утра" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _parse_datetime(text: str, reference_date: Optional[datetime] = None) -> Optional[datetime]:
    """
    Парсит дату/время из русского текста или ISO-строки.

    Примеры:
    - "2026-10-03T19:00:00" (так часто присылает Gemini)
    - "через 30 минут", "через десять минут", "через час", "через полчаса"
    - "завтра в 14:00", "послезавтра утром", "сегодня в 7 вечера"
    - "в пятницу в 10:00", "на следующей неделе в среду"

    Время без даты, которое сегодня уже прошло, — это завтра: «в 9 утра»,
    сказанное вечером, не должно попадать в прошлое.
    """
    if reference_date is None:
        reference_date = datetime.now()
    now = reference_date
    raw = (text or "").strip()
    if not raw:
        return None

    try:
        iso = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if iso.tzinfo is not None:
            iso = iso.astimezone().replace(tzinfo=None)
        return iso
    except ValueError:
        pass

    text = raw.lower().replace("ё", "е")

    rel = _relative(text, now)
    if rel is not None:
        return rel

    # ── Дата ─────────────────────────────────────────────────────────────────
    date_given = True
    same_weekday = False
    if "послезавтра" in text:
        target_date = now + timedelta(days=2)
    elif "завтра" in text:
        target_date = now + timedelta(days=1)
    elif "сегодня" in text:
        target_date = now
    elif "на следующей неделе" in text:
        target_date = now + timedelta(weeks=1)
    else:
        target_date = now
        date_given = False

    for stem, day_num in _WEEKDAYS.items():
        if re.search(rf"\b{stem}", text):
            days_ahead = (day_num - now.weekday() + 7) % 7
            if days_ahead == 0 and "на следующей неделе" in text:
                days_ahead = 7
            target_date = now + timedelta(days=days_ahead)
            same_weekday = days_ahead == 0
            date_given = True
            break

    # ── Время ────────────────────────────────────────────────────────────────
    hm = _clock_time(text)
    if hm is None:
        if "утром" in text:
            hm = (9, 0)
        elif "днём" in text or "днем" in text:
            hm = (14, 0)
        elif "вечером" in text:
            hm = (18, 0)
        else:
            hm = (9, 0)    # время не названо — 9:00 по умолчанию
    target_date = target_date.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)

    if target_date <= now:
        if same_weekday:                   # «в пятницу», сказанное в пятницу вечером
            target_date += timedelta(weeks=1)
        elif not date_given:
            target_date += timedelta(days=1)
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
                return load_json_or_quarantine(f)
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
        atomic_write_json(CALENDAR_FILE, data)
        return True
    except Exception as exc:
        _logger.error("Не сохранил %s: %s", CALENDAR_FILE.name, exc)
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


def take_due_reminders(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Напоминания, время которых пришло: отмечает их выполненными и отдаёт.

    Отметка и выдача — за один проход, чтобы одно напоминание не прозвучало
    дважды. Пропущенные, пока ПК был выключен, тоже отдаются (с флагом late).
    """
    now = now or datetime.now()
    calendar = _load_calendar()
    due = []
    for reminder in calendar.get("reminders", []):
        if reminder.get("completed"):
            continue
        try:
            when = datetime.fromisoformat(reminder["datetime"])
        except (KeyError, TypeError, ValueError):
            continue
        if when <= now:
            reminder["completed"] = True
            due.append({**reminder, "late": now - when > timedelta(minutes=5)})
    if due:
        _save_calendar(calendar)
    return due


_watch_thread: Optional[threading.Thread] = None


def start_reminder_watch(say, notify, interval: float = 15.0) -> None:
    """Фоновая проверка напоминаний: пришло время — голос Джарвиса и событие
    в журнале. Раньше напоминания только записывались, и не звучали никогда."""
    global _watch_thread
    if _watch_thread is not None and _watch_thread.is_alive():
        return

    def run():
        while True:
            try:
                for r in take_due_reminders():
                    text = r.get("text") or "без текста"
                    prefix = "Пропущенное напоминание" if r.get("late") else "Напоминаю"
                    notify("Напоминание", text)
                    say(f"{prefix}, сэр: {text}")
            except Exception as exc:
                _logger.warning("Проверка напоминаний: %s", exc, exc_info=True)
            time.sleep(interval)

    _watch_thread = threading.Thread(target=run, daemon=True, name="calendar-reminders")
    _watch_thread.start()


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
