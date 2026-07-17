"""
Действие: управление календарём.

Поддерживает:
- Локальный календарь (JSON)
- Google Calendar API (опционально)
- Синхронизация событий
"""

import sys
from pathlib import Path

from core.calendar_manager import (
    add_event,
    get_events,
    delete_event,
    update_event,
    add_reminder,
    get_upcoming_reminders,
    get_todays_schedule
)

import logging

_logger = logging.getLogger(__name__)


# ─── Пути ─────────────────────────────────────────────────────────────────────
def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE = _get_base_dir()
_API_CONFIG = _BASE / "config" / "api_keys.json"


# ─── Google Calendar API (опционально) ─────────────────────────────────────────
_GOOGLE_CALENDAR_ENABLED = False
_GOOGLE_CALENDAR_SERVICE = None

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    import pickle
    
    _SCOPES = ['https://www.googleapis.com/auth/calendar']
    _TOKEN_FILE = _BASE / "config" / "calendar_token.pickle"
    _CREDENTIALS_FILE = _BASE / "config" / "calendar_credentials.json"
    
    _GOOGLE_CALENDAR_ENABLED = True
except ImportError:
    pass


def _get_google_calendar_service():
    """Возвращает Google Calendar service если настроен."""
    global _GOOGLE_CALENDAR_SERVICE
    
    if not _GOOGLE_CALENDAR_ENABLED:
        return None
    
    if _GOOGLE_CALENDAR_SERVICE:
        return _GOOGLE_CALENDAR_SERVICE
    
    try:
        creds = None
        if _TOKEN_FILE.exists():
            with open(_TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)
        
        # Если нет валидных credentials, создаём новые
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif _CREDENTIALS_FILE.exists():
                flow = InstalledAppFlow.from_client_secrets_file(
                    _CREDENTIALS_FILE, _SCOPES
                )
                creds = flow.run_local_server(port=0)
                with open(_TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)
            else:
                return None
        
        _GOOGLE_CALENDAR_SERVICE = build('calendar', 'v3', credentials=creds)
        return _GOOGLE_CALENDAR_SERVICE
    
    except Exception as exc:
        _logger.warning("Подавлено исключение: %s", exc, exc_info=True)
        return None


def _sync_to_google_calendar(event_data: dict) -> bool:
    """Синхронизирует событие с Google Calendar."""
    try:
        service = _get_google_calendar_service()
        if not service:
            return False
        
        # Формируем событие для Google Calendar
        from datetime import datetime
        start = datetime.fromisoformat(event_data["start"])
        end = datetime.fromisoformat(event_data["end"])
        
        event = {
            'summary': event_data['title'],
            'description': event_data.get('description', ''),
            'location': event_data.get('location', ''),
            'start': {
                'dateTime': start.isoformat(),
                'timeZone': 'Europe/Moscow',
            },
            'end': {
                'dateTime': end.isoformat(),
                'timeZone': 'Europe/Moscow',
            },
        }
        
        service.events().insert(calendarId='primary', body=event).execute()
        return True
    
    except Exception:
        return False


def _sync_from_google_calendar() -> list:
    """Загружает события из Google Calendar."""
    try:
        service = _get_google_calendar_service()
        if not service:
            return []
        
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        time_min = now.isoformat() + 'Z'
        time_max = (now + timedelta(days=7)).isoformat() + 'Z'
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        return events_result.get('items', [])
    
    except Exception:
        return []


# ─── Публичная точка входа ────────────────────────────────────────────────────
def calendar(parameters: dict, player=None) -> str:
    """
    Главная точка входа для tool 'calendar'.
    
    parameters:
        action: add_event | get_events | delete_event | update_event | 
               add_reminder | todays_schedule | sync_google
        title: название события (для add_event)
        datetime: дата/время (для add_event, add_reminder)
        duration: длительность (для add_event, update_event)
        description: описание (для add_event)
        location: место (для add_event)
        text: текст напоминания (для add_reminder)
        date_range: today | tomorrow | week | all (для get_events)
        title_or_id: название или ID (для delete_event, update_event)
    """
    action = (parameters.get("action") or "").strip().lower()
    
    if player:
        player.write_log(f"SYS: 📅 Calendar — {action}")
    
    # ── Добавление события ─────────────────────────────────────────────────────
    if action in ("add_event", "add", "создать", "добавить", "создай событие"):
        title = parameters.get("title", "").strip()
        datetime_str = parameters.get("datetime", "").strip()
        duration = parameters.get("duration", "1 час").strip()
        description = parameters.get("description", "").strip()
        location = parameters.get("location", "").strip()
        
        if not title or not datetime_str:
            return "Укажите название и дату/время события, сэр."
        
        result = add_event(title, datetime_str, duration, description, location)
        
        # Если настроен Google Calendar, синхронизируем
        if "добавил" in result.lower():
            try:
                calendar_data = _load_calendar()
                events = calendar_data.get("events", [])
                if events:
                    last_event = events[-1]
                    if _sync_to_google_calendar(last_event):
                        result += " Синхронизировано с Google Calendar."
            except Exception as exc:
                _logger.warning("Подавлено исключение: %s", exc, exc_info=True)
        
        return result
    
    # ── Получение событий ─────────────────────────────────────────────────────
    elif action in ("get_events", "get", "показать", "события", "какие дела"):
        date_range = parameters.get("date_range", "today").strip().lower()
        
        # Парсим дату из запроса
        if "завтра" in date_range or "tomorrow" in date_range:
            date_range = "tomorrow"
        elif "недел" in date_range or "week" in date_range:
            date_range = "week"
        elif "все" in date_range or "all" in date_range:
            date_range = "all"
        else:
            date_range = "today"
        
        return get_events(date_range)
    
    # ── Удаление события ───────────────────────────────────────────────────────
    elif action in ("delete_event", "delete", "удалить", "отмени", "отменить"):
        title_or_id = parameters.get("title_or_id", "").strip()
        
        if not title_or_id:
            return "Укажите название или ID события для удаления, сэр."
        
        return delete_event(title_or_id)
    
    # ── Обновление события ─────────────────────────────────────────────────────
    elif action in ("update_event", "update", "обновить", "изменить", "перенеси"):
        title_or_id = parameters.get("title_or_id", "").strip()
        new_datetime = parameters.get("new_datetime", "").strip()
        new_duration = parameters.get("new_duration", "").strip()
        
        if not title_or_id:
            return "Укажите название или ID события для обновления, сэр."
        
        return update_event(title_or_id, new_datetime, new_duration)
    
    # ── Добавление напоминания ─────────────────────────────────────────────────
    elif action in ("add_reminder", "reminder", "напомни", "напоминание"):
        text = parameters.get("text", "").strip()
        datetime_str = parameters.get("datetime", "").strip()
        
        if not text or not datetime_str:
            return "Укажите текст напоминания и когда напомнить, сэр."
        
        return add_reminder(text, datetime_str)
    
    # ── Расписание на сегодня ───────────────────────────────────────────────────
    elif action in ("todays_schedule", "schedule", "расписание", "сегодня"):
        return get_todays_schedule()
    
    # ── Синхронизация с Google Calendar ────────────────────────────────────────
    elif action in ("sync_google", "sync", "синхронизируй"):
        if not _GOOGLE_CALENDAR_ENABLED:
            return "Google Calendar не настроен, сэр. Нужно установить google-api-python-client и создать credentials.json."
        
        google_events = _sync_from_google_calendar()
        if not google_events:
            return "Google Calendar пуст или не удалось загрузить события, сэр."
        
        result = f"Загрузил {len(google_events)} событий из Google Calendar:\n"
        for event in google_events[:10]:  # Показываем первые 10
            summary = event.get('summary', 'Без названия')
            start = event.get('start', {}).get('dateTime', '')
            result += f"  - {summary}: {start}\n"
        
        if len(google_events) > 10:
            result += f"  ... и ещё {len(google_events) - 10} событий"
        
        return result
    
    else:
        return f"Не понял команду: «{action}». Доступные: add_event, get_events, delete_event, update_event, add_reminder, todays_schedule, sync_google"


def _load_calendar():
    """Вспомогательная функция для загрузки календаря."""
    from core.calendar_manager import _load_calendar
    return _load_calendar()


def get_calendar_context() -> str:
    """
    Возвращает контекст календаря для proactive engine.
    
    Используется для:
    - Утреннего брифинга
    - Напоминаний перед встречами
    - Проверки конфликтов
    """
    try:
        schedule = get_todays_schedule()
        upcoming = get_upcoming_reminders(minutes_ahead=60)
        
        context = f"📅 {schedule}\n"
        
        if upcoming:
            context += "\n🔔 Ближайшие напоминания:\n"
            for reminder in upcoming:
                from datetime import datetime
                reminder_time = datetime.fromisoformat(reminder["datetime"])
                time_str = reminder_time.strftime("%H:%M")
                context += f"  {time_str} — {reminder['text']}\n"
        
        return context
    
    except Exception:
        return ""
