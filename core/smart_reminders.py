"""
Smart Reminders Engine — умная система напоминаний.

Функциональность:
- Адаптивные напоминания (обучение паттернам пользователя)
- Эмоциональный интеллект (адаптация тона и времени)
- Интеграция с Spotify (приглушение перед встречей)
- Интеграция с Кино (предложение приостановить)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import random


# ─── Пути ─────────────────────────────────────────────────────────────────────
def _get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE = _get_base_dir()
_PATTERNS_FILE = _BASE / "config" / "reminder_patterns.json"


# ─── Загрузка/сохранение паттернов ───────────────────────────────────────────────
def _load_patterns() -> Dict[str, Any]:
    """Загружает паттерны пользователя из файла."""
    if not _PATTERNS_FILE.exists():
        return {
            "reminder_times": {},  # Оптимальные времена напоминаний для разных типов событий
            "ignore_patterns": {},  # Когда пользователь игнорирует напоминания
            "preferred_tone": "neutral",  # Предпочитаемый тон
            "stress_adaptation": True,  # Адаптация к стрессу
            "music_integration": True,  # Интеграция с музыкой
            "movie_integration": True,  # Интеграция с кино
            "response_history": []  # История реакций на напоминания
        }
    
    try:
        with open(_PATTERNS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return _get_default_patterns()


def _save_patterns(patterns: Dict[str, Any]) -> bool:
    """Сохраняет паттерны пользователя в файл."""
    try:
        _PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_PATTERNS_FILE, 'w', encoding='utf-8') as f:
            json.dump(patterns, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _get_default_patterns() -> Dict[str, Any]:
    """Возвращает паттерны по умолчанию."""
    return {
        "reminder_times": {
            "meeting": 15,  # По умолчанию за 15 минут
            "deadline": 60,  # За 1 час
            "personal": 30,  # За 30 минут
            "work": 15  # За 15 минут
        },
        "ignore_patterns": {
            "morning": [],  # Время когда пользователь игнорирует напоминания утром
            "evening": [],  # Время когда пользователь игнорирует напоминания вечером
            "weekend": False  # Игнорирует ли на выходных
        },
        "preferred_tone": "neutral",
        "stress_adaptation": True,
        "music_integration": True,
        "movie_integration": True,
        "response_history": []
    }


# ─── Отслеживание паттернов пользователя ────────────────────────────────────────
class UserPatternTracker:
    """Отслеживает паттерны поведения пользователя для адаптивных напоминаний."""
    
    def __init__(self):
        self.patterns = _load_patterns()
    
    def record_response(self, event_type: str, reminder_time: datetime, 
                       response: str, context: Dict[str, Any]):
        """
        Записывает реакцию пользователя на напоминание.
        
        Args:
            event_type: Тип события (meeting, deadline, personal, work)
            reminder_time: Время напоминания
            response: Реакция (acknowledged, ignored, dismissed, snoozed)
            context: Контекст (lifestyle_mode, emotion, current_activity)
        """
        record = {
            "event_type": event_type,
            "reminder_time": reminder_time.isoformat(),
            "response": response,
            "context": context,
            "timestamp": datetime.now().isoformat()
        }
        
        self.patterns["response_history"].append(record)
        
        # Ограничиваем историю последними 100 записями
        if len(self.patterns["response_history"]) > 100:
            self.patterns["response_history"] = self.patterns["response_history"][-100:]
        
        _save_patterns(self.patterns)
    
    def get_optimal_reminder_time(self, event_type: str, 
                                 event_time: datetime) -> datetime:
        """
        Вычисляет оптимальное время напоминания на основе паттернов.
        
        Args:
            event_type: Тип события
            event_time: Время события
            
        Returns:
            Оптимальное время напоминания
        """
        # Базовое время из паттернов
        default_minutes = self.patterns["reminder_times"].get(event_type, 15)
        
        # Анализируем историю реакций
        recent_responses = [
            r for r in self.patterns["response_history"]
            if r["event_type"] == event_type
        ]
        
        if len(recent_responses) >= 5:
            # Если пользователь часто игнорирует напоминания за 15 минут
            ignored_count = sum(
                1 for r in recent_responses[-10:]
                if r["response"] == "ignored"
            )
            
            if ignored_count >= 7:  # 70% игнорирует
                # Увеличиваем время напоминания
                default_minutes = min(default_minutes + 15, 60)
            elif ignored_count <= 2:  # 20% игнорирует
                # Уменьшаем время напоминания
                default_minutes = max(default_minutes - 5, 5)
        
        return event_time - timedelta(minutes=default_minutes)
    
    def should_remind_now(self, event_time: datetime, 
                         current_context: Dict[str, Any]) -> bool:
        """
        Решает, стоит ли напоминать сейчас на основе контекста.
        
        Args:
            event_time: Время события
            current_context: Текущий контекст (mode, emotion, activity)
            
        Returns:
            True если стоит напомнить, False если нет
        """
        # Проверка на стресс
        if (self.patterns["stress_adaptation"] and 
            current_context.get("emotion") in ["stressed", "overwhelmed"]):
            # Если пользователь в стрессе и событие не критическое
            if current_context.get("event_priority", "normal") != "critical":
                return False
        
        # Проверка на lifestyle-режим
        mode = current_context.get("lifestyle_mode", "")
        if mode == "movie" and self.patterns["movie_integration"]:
            # Не прерывать во время фильма, если не критическое событие
            if current_context.get("event_priority", "normal") != "critical":
                return False
        
        # Проверка на время суток (игнор паттерны)
        now = datetime.now()
        hour = now.hour
        
        if (7 <= hour < 10 and  # Утро
            self.patterns["ignore_patterns"].get("morning_ignore", False)):
            return False
        
        if (20 <= hour < 23 and  # Вечер
            self.patterns["ignore_patterns"].get("evening_ignore", False)):
            return False
        
        # Проверка на выходные
        if now.weekday() >= 5 and self.patterns["ignore_patterns"].get("weekend", False):
            return False
        
        return True


# ─── Эмоциональный интеллект ───────────────────────────────────────────────────
class EmotionalReminderAdapter:
    """Адаптирует напоминания на основе эмоционального состояния."""
    
    def __init__(self):
        self.tone_mapping = {
            "happy": ["весёлый", "энергичный"],
            "neutral": ["спокойный", "нейтральный"],
            "sad": ["мягкий", "поддерживающий"],
            "stressed": ["спокойный", "облегчающий"],
            "overwhelmed": ["очень мягкий", "не давящий"],
            "focused": ["краткий", "прямой"],
            "tired": ["мягкий", "понимающий"]
        }
    
    def adapt_reminder_text(self, base_text: str, emotion: str, 
                          event_priority: str = "normal") -> str:
        """
        Адаптирует текст напоминания на основе эмоции.
        
        Args:
            base_text: Базовый текст напоминания
            emotion: Эмоциональное состояние
            event_priority: Приоритет события (critical, normal, low)
            
        Returns:
            Адаптированный текст
        """
        # Если событие критическое — не адаптировать
        if event_priority == "critical":
            return base_text
        
        # Добавляем эмоциональный контекст
        emotion_prefixes = {
            "happy": ["Отлично! ", "Замечательно! "],
            "neutral": ["", ""],
            "sad": ["Понимаю, ", "Знаю, "],
            "stressed": ["Постарайся не волноваться, ", "Всё будет хорошо, "],
            "overwhelmed": ["Давай по порядку, ", "Одно за другим, "],
            "focused": ["Кратко: ", "Сразу к делу: "],
            "tired": ["Отдохни немного, ", "Возьми паузу, "]
        }
        
        prefix = random.choice(emotion_prefixes.get(emotion, [""]))
        
        # Если пользователь устал или в стрессе — добавляем поддержку
        if emotion in ["tired", "stressed", "overwhelmed"]:
            suffix = " Не забывай отдыхать!"
        else:
            suffix = ""
        
        return f"{prefix}{base_text}{suffix}"
    
    def should_defer_reminder(self, emotion: str, 
                             event_priority: str) -> bool:
        """
        Решает, стоит ли отложить напоминание.
        
        Args:
            emotion: Эмоциональное состояние
            event_priority: Приоритет события
            
        Returns:
            True если стоит отложить, False если нет
        """
        # Критические события всегда напоминаем
        if event_priority == "critical":
            return False
        
        # Если пользователь в сильном стрессе — отложим некритичные напоминания
        if emotion in ["overwhelmed"]:
            return True
        
        # Если пользователь устал — отложим низкоприоритетные напоминания
        if emotion == "tired" and event_priority == "low":
            return True
        
        return False


# ─── Интеграция с внешними системами ─────────────────────────────────────────────
class IntegrationManager:
    """Управляет интеграцией со Spotify и Кино."""
    
    def __init__(self):
        self.patterns = _load_patterns()
    
    def prepare_for_event(self, event_context: Dict[str, Any]) -> List[str]:
        """
        Подготавливает окружение к событию.
        
        Args:
            event_context: Контекст события (time_until, priority, current_activity)
            
        Returns:
            Список выполненных действий
        """
        actions = []
        time_until = event_context.get("time_until", 0)  # минут до события
        current_activity = event_context.get("current_activity", "")
        priority = event_context.get("priority", "normal")
        
        # Spotify интеграция
        if (self.patterns["music_integration"] and 
            current_activity == "music" and 
            time_until <= 5 and  # За 5 минут
            priority != "low"):
            actions.append("spotify_pause_or_lower")
        
        # Кино интеграция
        if (self.patterns["movie_integration"] and 
            current_activity == "movie" and 
            time_until <= 10 and  # За 10 минут
            priority != "low"):
            actions.append("movie_pause_suggestion")
        
        return actions
    
    def get_spotify_action(self) -> str:
        """
        Возвращает действие для Spotify.
        
        Returns:
            Действие: pause, lower_volume, или none
        """
        return "pause"  # Для MVP — всегда пауза
    
    def get_movie_action(self) -> str:
        """
        Возвращает действие для Кино.
        
        Returns:
            Действие: pause, suggestion, или none
        """
        return "suggestion"  # Для MVP — предложение приостановить


# ─── Основной класс умных напоминаний ───────────────────────────────────────────
class SmartReminderEngine:
    """Основной класс для умных напоминаний."""
    
    def __init__(self):
        self.pattern_tracker = UserPatternTracker()
        self.emotional_adapter = EmotionalReminderAdapter()
        self.integration_manager = IntegrationManager()
    
    def generate_smart_reminder(self, event: Dict[str, Any], 
                               current_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Генерирует умное напоминание для события.
        
        Args:
            event: Событие из календаря
            current_context: Текущий контекст (emotion, lifestyle_mode, activity)
            
        Returns:
            Словарь с напоминанием (text, tone, actions, should_remind)
        """
        event_time = datetime.fromisoformat(event["start"])
        event_type = self._classify_event_type(event)
        event_priority = self._classify_event_priority(event)
        
        # Определяем оптимальное время напоминания
        optimal_time = self.pattern_tracker.get_optimal_reminder_time(
            event_type, event_time
        )
        
        # Проверяем стоит ли напоминать сейчас.
        # Приоритет кладём в контекст явно: should_remind_now читает его оттуда,
        # и без этого критичные события молча душились стрессом и режимом фильма.
        should_remind = self.pattern_tracker.should_remind_now(
            event_time, {**current_context, "event_priority": event_priority}
        )
        
        # Эмоциональная адаптация
        emotion = current_context.get("emotion", "neutral")
        base_text = self._generate_base_reminder_text(event)
        adapted_text = self.emotional_adapter.adapt_reminder_text(
            base_text, emotion, event_priority
        )
        
        # Проверяем нужно ли отложить
        should_defer = self.emotional_adapter.should_defer_reminder(
            emotion, event_priority
        )
        
        # Интеграции
        time_until = (event_time - datetime.now()).total_seconds() / 60
        integration_actions = self.integration_manager.prepare_for_event({
            "time_until": time_until,
            "priority": event_priority,
            "current_activity": current_context.get("activity", "")
        })
        
        return {
            "text": adapted_text,
            "tone": self.emotional_adapter.tone_mapping.get(emotion, ["neutral"])[0],
            "optimal_reminder_time": optimal_time.isoformat(),
            "should_remind": should_remind and not should_defer,
            "should_defer": should_defer,
            "integration_actions": integration_actions,
            "event_type": event_type,
            "priority": event_priority
        }
    
    def record_reminder_response(self, reminder: Dict[str, Any], 
                                response: str):
        """
        Записывает реакцию пользователя на напоминание.
        
        Args:
            reminder: Напоминание
            response: Реакция (acknowledged, ignored, dismissed, snoozed)
        """
        self.pattern_tracker.record_response(
            reminder["event_type"],
            datetime.fromisoformat(reminder["optimal_reminder_time"]),
            response,
            reminder.get("context", {})
        )
    
    def _classify_event_type(self, event: Dict[str, Any]) -> str:
        """Классифицирует тип события."""
        title = event["title"].lower()
        description = event.get("description", "").lower()
        
        # Ключевые слова для классификации
        work_keywords = ["встреча", "созвон", "митинг", "работа", "проект", "клиент"]
        deadline_keywords = ["дедлайн", "срок", "сдача", "отчёт", "презентация"]
        personal_keywords = ["день рождения", "ужин", "вечеринка", "отдых", "спорт"]
        
        text = title + " " + description
        
        if any(kw in text for kw in work_keywords):
            return "work"
        elif any(kw in text for kw in deadline_keywords):
            return "deadline"
        elif any(kw in text for kw in personal_keywords):
            return "personal"
        else:
            return "meeting"
    
    def _classify_event_priority(self, event: Dict[str, Any]) -> str:
        """Классифицирует приоритет события."""
        title = event["title"].lower()
        description = event.get("description", "").lower()
        
        # Ключевые слова для приоритета
        critical_keywords = ["важно", "срочно", "критично", "дедлайн", "экзамен"]
        low_keywords = ["опционально", "по желанию", "можно пропустить"]
        
        text = title + " " + description
        
        if any(kw in text for kw in critical_keywords):
            return "critical"
        elif any(kw in text for kw in low_keywords):
            return "low"
        else:
            return "normal"
    
    def _generate_base_reminder_text(self, event: Dict[str, Any]) -> str:
        """Генерирует базовый текст напоминания."""
        title = event["title"]
        start_time = datetime.fromisoformat(event["start"])
        description = event.get("description", "")
        location = event.get("location", "")
        
        # Формируем текст
        text_parts = [f"Напоминаю: {title} в {start_time.strftime('%H:%M')}"]

        if description:
            text_parts.append(f"({description})")
        
        if location:
            text_parts.append(f"Место: {location}")
        
        return ". ".join(text_parts)


# Дольше этого «сессия» — не марафон, а незакрытый счётчик: приложение
# закрыли, не завершив активность, и открыли позже. Такую сессию сбрасываем.
_MAX_PLAUSIBLE_SESSION_MIN = 8 * 60


# ─── Отслеживание активности ──────────────────────────────────────────────────────
class ActivityTracker:
    """Отслеживает время активности для health напоминаний."""
    
    def __init__(self):
        self.activity_file = _BASE / "config" / "activity_tracking.json"
        self.activities = self._load_activities()
    
    def _load_activities(self) -> Dict[str, Any]:
        """Загружает данные об активности."""
        if not self.activity_file.exists():
            return {
                "current_session": None,  # {activity_type, start_time}
                "sessions": [],  # История сессий
                "health_alerts": []  # История health-уведомлений
            }
        
        try:
            with open(self.activity_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return self._get_default_activities()
    
    def _get_default_activities(self) -> Dict[str, Any]:
        """Возвращает данные по умолчанию."""
        return {
            "current_session": None,
            "sessions": [],
            "health_alerts": []
        }
    
    def _save_activities(self) -> bool:
        """Сохраняет данные об активности."""
        try:
            self.activity_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.activity_file, 'w', encoding='utf-8') as f:
                json.dump(self.activities, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False
    
    def start_activity(self, activity_type: str) -> bool:
        """
        Начинает отслеживание активности.
        
        Args:
            activity_type: Тип активности (movie, work, music, etc.)
            
        Returns:
            True если успешно
        """
        self.activities = self._load_activities()
        
        # Если уже есть активная сессия — завершаем её
        if self.activities["current_session"]:
            self.end_activity()
        
        self.activities["current_session"] = {
            "activity_type": activity_type,
            "start_time": datetime.now().isoformat()
        }
        
        return self._save_activities()
    
    def end_activity(self) -> Dict[str, Any]:
        """
        Завершает текущую активность и сохраняет сессию.
        
        Returns:
            Информация о завершённой сессии
        """
        if not self.activities["current_session"]:
            return {"duration_minutes": 0}
        
        session = self.activities["current_session"]
        start_time = datetime.fromisoformat(session["start_time"])
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds() / 60
        
        session_info = {
            "activity_type": session["activity_type"],
            "start_time": session["start_time"],
            "end_time": end_time.isoformat(),
            "duration_minutes": duration
        }
        
        self.activities["sessions"].append(session_info)
        self.activities["current_session"] = None
        
        # Ограничиваем историю последними 100 сессиями
        if len(self.activities["sessions"]) > 100:
            self.activities["sessions"] = self.activities["sessions"][-100:]
        
        self._save_activities()
        return session_info
    
    def get_current_duration(self) -> float:
        """
        Возвращает длительность текущей активности в минутах.
        
        Returns:
            Длительность в минутах
        """
        if not self.activities["current_session"]:
            return 0.0
        
        start_time = datetime.fromisoformat(self.activities["current_session"]["start_time"])
        duration = (datetime.now() - start_time).total_seconds() / 60
        # Залипшая сессия (закрыли приложение, не завершив просмотр) — сбрасываем,
        # иначе Джарвис заявит «вы смотрите фильм уже 714 часов».
        if duration > _MAX_PLAUSIBLE_SESSION_MIN:
            self.activities["current_session"] = None
            self._save_activities()
            return 0.0
        return duration
    
    def get_activity_type(self) -> Optional[str]:
        """
        Возвращает тип текущей активности.
        
        Returns:
            Тип активности или None
        """
        if self.activities["current_session"]:
            return self.activities["current_session"]["activity_type"]
        return None
    
    def record_health_alert(self, alert_type: str, message: str):
        """
        Записывает health-уведомление.
        
        Args:
            alert_type: Тип уведомления (movie_too_long, work_no_break)
            message: Сообщение
        """
        alert = {
            "alert_type": alert_type,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }
        
        self.activities["health_alerts"].append(alert)
        
        # Ограничиваем историю последними 50 уведомлениями
        if len(self.activities["health_alerts"]) > 50:
            self.activities["health_alerts"] = self.activities["health_alerts"][-50:]
        
        self._save_activities()


# ─── Health-focused напоминания ───────────────────────────────────────────────────
class HealthReminderEngine:
    """Генерирует health-focused напоминания."""
    
    def __init__(self):
        self.activity_tracker = ActivityTracker()
    
    def check_health_needs(self, current_context: Dict[str, Any]) -> str:
        """
        Проверяет нужны ли health-напоминания.
        
        Args:
            current_context: Текущий контекст
            
        Returns:
            Health-напоминание как строка или None
        """
        reminders = []
        
        activity_type = self.activity_tracker.get_activity_type()
        current_duration = self.activity_tracker.get_current_duration()
        
        # Проверка на долгий просмотр фильма (>2 часов)
        if activity_type == "movie" and current_duration > 120:
            reminder = self._generate_movie_health_reminder(current_duration)
            reminders.append(reminder.get("message", ""))
        
        # Проверка на долгую работу без перерыва (>1 часа)
        if activity_type == "work" and current_duration > 60:
            reminder = self._generate_work_break_reminder(current_duration)
            reminders.append(reminder.get("message", ""))
        
        return reminders[0] if reminders else None
    
    def _generate_movie_health_reminder(self, duration: float) -> Dict[str, Any]:
        """
        Генерирует напоминание о долгом просмотре фильма.
        
        Args:
            duration: Длительность в минутах
            
        Returns:
            Напоминание
        """
        hours = int(duration / 60)
        minutes = int(duration % 60)
        
        # Варианты сообщений
        messages = [
            f"Сэр, вы уже {hours} час{'а' if hours > 1 else ''} {minutes} минут смотрите фильм. Это вредно для глаз и осанки.",
            f"Сэр, рекомендуется сделать перерыв. Вы смотрите фильм уже {hours} час{'а' if hours > 1 else ''}.",
            f"Сэр, для здоровья полезно встать и размяться. Вы смотрите уже {hours} час{'а' if hours > 1 else ''}."
        ]
        
        return {
            "type": "movie_health",
            "priority": "medium",
            "message": messages[0] if hours >= 3 else messages[1],
            "suggestions": ["сделать перерыв", "размяться", "встать и пройтись"],
            "duration_minutes": duration
        }
    
    def _generate_work_break_reminder(self, duration: float) -> Dict[str, Any]:
        """
        Генерирует напоминание о перерыве в работе.
        
        Args:
            duration: Длительность в минутах
            
        Returns:
            Напоминание
        """
        hours = int(duration / 60)
        minutes = int(duration % 60)
        
        # Варианты сообщений
        if hours >= 2:
            messages = [
                f"Сэр, вы работаете уже {hours} час{'а' if hours > 1 else ''}. Настоятельно нужен перерыв!",
                f"Сэр, для продуктивности полезно делать перерыв каждый час. Вы работаете {hours} час{'а' if hours > 1 else ''}."
            ]
        else:
            messages = [
                f"Сэр, вы работаете уже {minutes} минут. Хотите сделать короткий перерыв?",
                f"Сэр, рекомендую 5-минутную разминку. Вы работаете {minutes} минут.",
                f"Сэр, встаньте и разомнитесь. Это улучшит концентрацию."
            ]
        
        suggestions = ["сделать перерыв", "разминка 5 минут", "встать и пройтись"]
        
        return {
            "type": "work_break",
            "priority": "high" if hours >= 2 else "medium",
            "message": messages[0] if hours >= 2 else messages[1],
            "suggestions": suggestions,
            "duration_minutes": duration
        }


# ─── Удобный интерфейс ───────────────────────────────────────────────────────────
def generate_smart_reminder(event: Dict[str, Any], 
                           current_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Удобная функция для генерации умного напоминания.
    
    Args:
        event: Событие из календаря
        current_context: Текущий контекст
        
    Returns:
        Словарь с напоминанием
    """
    engine = SmartReminderEngine()
    return engine.generate_smart_reminder(event, current_context)


def record_reminder_response(reminder: Dict[str, Any], response: str):
    """
    Удобная функция для записи реакции на напоминание.
    
    Args:
        reminder: Напоминание
        response: Реакция
    """
    engine = SmartReminderEngine()
    engine.record_reminder_response(reminder, response)


# ─── Удобные функции для активности ───────────────────────────────────────────────
def start_activity_tracking(activity_type: str) -> bool:
    """
    Начинает отслеживание активности.
    
    Args:
        activity_type: Тип активности (movie, work, music, etc.)
        
    Returns:
        True если успешно
    """
    tracker = ActivityTracker()
    return tracker.start_activity(activity_type)


def end_activity_tracking() -> Dict[str, Any]:
    """
    Завершает отслеживание активности.
    
    Returns:
    Информация о завершённой сессии
    """
    tracker = ActivityTracker()
    return tracker.end_activity()


def get_current_activity_duration() -> float:
    """
    Возвращает длительность текущей активности в минутах.
    
    Returns:
    Длительность в минутах
    """
    tracker = ActivityTracker()
    return tracker.get_current_duration()


def get_current_activity_type() -> Optional[str]:
    """
    Возвращает тип текущей активности.
    
    Returns:
    Тип активности или None
    """
    tracker = ActivityTracker()
    return tracker.get_activity_type()


# ─── Удобные функции для health напоминаний ─────────────────────────────────────────
def check_health_needs(current_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Проверяет нужны ли health-напоминания.
    
    Args:
        current_context: Текущий контекст
        
    Returns:
        Список health-напоминаний
    """
    engine = HealthReminderEngine()
    return engine.check_health_needs(current_context)
