"""
Прогнозирование потребностей и проактивные действия
Предсказывает что нужно пользователю до того как он попросит
"""

import json
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime, timedelta

# Импорт календаря для проактивных уведомлений
try:
    from actions.calendar import get_upcoming_reminders, get_todays_schedule
    CALENDAR_AVAILABLE = True
except ImportError:
    CALENDAR_AVAILABLE = False

# Импорт умных напоминаний
try:
    from core.smart_reminders import generate_smart_reminder, record_reminder_response
    SMART_REMINDERS_AVAILABLE = True
except ImportError:
    SMART_REMINDERS_AVAILABLE = False

# Импорт health-напоминаний
try:
    from core.smart_reminders import check_health_needs, start_activity_tracking, end_activity_tracking
    HEALTH_REMINDERS_AVAILABLE = True
except ImportError:
    HEALTH_REMINDERS_AVAILABLE = False


class ProactiveEngine:
    """
    Движок проактивного прогнозирования
    Предсказывает потребности пользователя на основе паттернов
    """

    def __init__(self, base_dir: Path):
        self.patterns_path = base_dir / "config" / "usage_patterns.json"
        self.patterns = self._load_patterns()
        self.shown_suggestions = set()  # Чтобы не повторять показанные предложения

    def _load_patterns(self) -> Dict:
        """Загружает паттерны использования из файла"""
        if self.patterns_path.exists():
            try:
                with open(self.patterns_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        # Паттерны по умолчанию
        return {
            "time_patterns": {
                "morning": {
                    "hour_range": [6, 10],
                    "common_actions": ["weather", "news", "calendar", "email"],
                    "suggestions": [
                        "Сэр, доброе утро! Хотите узнать погоду на сегодня?",
                        "Сэр, проверить календарь на сегодня?",
                        "Сэр, открыть почту?"
                    ]
                },
                "work_start": {
                    "hour_range": [9, 11],
                    "common_actions": ["open_app", "browser", "files"],
                    "suggestions": [
                        "Сэр, начать рабочий день? Открыть нужные приложения?",
                        "Сэр, запустить рабочие инструменты?"
                    ]
                },
                "lunch": {
                    "hour_range": [12, 14],
                    "common_actions": ["food", "break", "music"],
                    "suggestions": [
                        "Сэр, обеденное время. Может, сделать перерыв?",
                        "Сэр, включить музыку на обед?"
                    ]
                },
                "afternoon": {
                    "hour_range": [14, 17],
                    "common_actions": ["work", "focus", "meeting"],
                    "suggestions": [
                        "Сэр, продолжить работу?",
                        "Сэр, запланировать встречу?"
                    ]
                },
                "evening": {
                    "hour_range": [18, 22],
                    "common_actions": ["movie", "music", "relax", "social"],
                    "suggestions": [
                        "Сэр, вечер! Чем заняться? Фильм или музыка?",
                        "Сэр, расслабиться после работы?",
                        "Сэр, проверить социальные сети?"
                    ]
                },
                "night": {
                    "hour_range": [22, 6],
                    "common_actions": ["sleep", "shutdown", "relax"],
                    "suggestions": [
                        "Сэр, поздно. Может, пора спать?",
                        "Сэр, завершить работу на сегодня?"
                    ]
                }
            },
            "sequence_patterns": {
                "work_session": {
                    "sequence": ["open_app", "browser", "files"],
                    "suggestion": "Сэр, запустить рабочий набор приложений?"
                },
                "movie_session": {
                    "sequence": ["movie_player", "dim_lights"],
                    "suggestion": "Сэр, создать атмосферу для кино? Убавить яркость?"
                },
                "music_session": {
                    "sequence": ["music_player", "relax"],
                    "suggestion": "Сэр, включить фоновую музыку для работы?"
                }
            },
            "frequency_patterns": {
                "daily": {},
                "weekly": {},
                "context_based": {}
            }
        }

    def _save_patterns(self):
        """Сохраняет паттерны в файл"""
        try:
            self.patterns_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.patterns_path, "w", encoding="utf-8") as f:
                json.dump(self.patterns, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ProactiveEngine] Ошибка сохранения: {e}")

    def record_action(self, action: str, context: Dict):
        """
        Записывает действие для анализа паттернов

        Args:
            action: Выполненное действие
            context: Контекст (время, режим, эмоция)
        """
        now = datetime.now()
        hour = now.hour

        # Определяем период времени
        period = self._get_time_period(hour)

        # Обновляем частоту действий по времени
        if period not in self.patterns["frequency_patterns"]["daily"]:
            self.patterns["frequency_patterns"]["daily"][period] = {}

        if action not in self.patterns["frequency_patterns"]["daily"][period]:
            self.patterns["frequency_patterns"]["daily"][period][action] = 0

        self.patterns["frequency_patterns"]["daily"][period][action] += 1

        # Обновляем последовательности
        if "last_actions" not in self.patterns:
            self.patterns["last_actions"] = []

        self.patterns["last_actions"].append({
            "action": action,
            "timestamp": now.isoformat(),
            "context": context
        })

        # Ограничиваем историю
        if len(self.patterns["last_actions"]) > 100:
            self.patterns["last_actions"] = self.patterns["last_actions"][-100:]

        self._save_patterns()

    def _get_time_period(self, hour: int) -> str:
        """Определяет период времени по часу"""
        if 6 <= hour < 10:
            return "morning"
        elif 9 <= hour < 11:
            return "work_start"
        elif 12 <= hour < 14:
            return "lunch"
        elif 14 <= hour < 17:
            return "afternoon"
        elif 18 <= hour < 22:
            return "evening"
        else:
            return "night"

    def predict_need(self, current_context: Dict) -> Optional[str]:
        """
        Прогнозирует потребность пользователя на основе контекста

        Args:
            current_context: Текущий контекст (время, режим, эмоция, активность)

        Returns:
            Строка с проактивным предложением или None
        """
        now = datetime.now()
        hour = now.hour
        period = self._get_time_period(hour)

        # Проверяем паттерны по времени
        if period in self.patterns["time_patterns"]:
            time_pattern = self.patterns["time_patterns"][period]

            # Проверяем что мы в нужном часовом диапазоне
            hour_range = time_pattern["hour_range"]
            if hour_range[0] <= hour < hour_range[1]:
                # Проверяем частоту действий в этот период
                daily_patterns = self.patterns["frequency_patterns"].get("daily", {})
                if period in daily_patterns:
                    # Находим самое частое действие
                    actions = daily_patterns[period]
                    if actions:
                        most_common = max(actions, key=actions.get)
                        # Предлагаем только если действие выполняется часто (>3 раза)
                        if actions[most_common] > 3:
                            suggestions = time_pattern["suggestions"]
                            if suggestions:
                                return suggestions[0]

        # Проверяем последовательности
        last_actions = self.patterns.get("last_actions", [])
        if len(last_actions) >= 2:
            recent = [a["action"] for a in last_actions[-3:]]

            for pattern_name, pattern_data in self.patterns["sequence_patterns"].items():
                sequence = pattern_data["sequence"]
                # Проверяем совпадение последовательности
                if all(action in recent for action in sequence):
                    return pattern_data["suggestion"]

        return None

    def get_proactive_suggestions(self, context: Dict) -> List[str]:
        """
        Генерирует несколько проактивных предложений

        Args:
            context: Текущий контекст

        Returns:
            Список предложений
        """
        suggestions = []

        # Предсказание потребности
        need_prediction = self.predict_need(context)
        if need_prediction:
            suggestions.append(need_prediction)

        # Календарные напоминания
        if CALENDAR_AVAILABLE:
            calendar_suggestions = self._get_calendar_suggestions()
            suggestions.extend(calendar_suggestions)

        # Умные напоминания (с эмоциональным интеллектом)
        if SMART_REMINDERS_AVAILABLE and CALENDAR_AVAILABLE:
            smart_suggestions = self._get_smart_reminder_suggestions(context)
            suggestions.extend(smart_suggestions)

        # Health-напоминания (фильм > 2 часов, работа > 1 час)
        if HEALTH_REMINDERS_AVAILABLE:
            health_suggestions = self._get_health_suggestions(context)
            suggestions.extend(health_suggestions)

        # Контекстные предложения
        current_activity = context.get("current_activity")
        current_mode = context.get("mode", "normal")
        last_emotion = context.get("last_emotion")

        # Если долгое бездействие
        if not current_activity and current_mode == "normal":
            suggestions.append("Сэр, чем могу помочь?")

        # Если режим работы давно активен
        if current_mode == "work" and current_activity:
            suggestions.append("Сэр, нужен перерыв?")

        # Дедупликация и фильтрация показанных
        unique_suggestions = []
        seen = set()
        for suggestion in suggestions:
            if suggestion not in seen and suggestion not in self.shown_suggestions:
                unique_suggestions.append(suggestion)
                seen.add(suggestion)
                self.shown_suggestions.add(suggestion)

        # Ограничиваем показанные (чтобы set не рос бесконечно)
        if len(self.shown_suggestions) > 50:
            self.shown_suggestions = set(list(self.shown_suggestions)[-25:])

        return unique_suggestions[:3]  # Максимум 3 предложения

    def _get_calendar_suggestions(self) -> List[str]:
        """
        Получает проактивные предложения из календаря

        Returns:
            Список календарных предложений
        """
        suggestions = []

        try:
            # Проверяем ближайшие напоминания (в течение 15 минут)
            upcoming = get_upcoming_reminders(minutes_ahead=15)
            for reminder in upcoming:
                reminder_time = datetime.fromisoformat(reminder["datetime"])
                time_str = reminder_time.strftime("%H:%M")
                suggestions.append(f"Сэр, напоминание через {time_str}: {reminder['text']}")

            # Если утром — утренний брифинг
            now = datetime.now()
            if 6 <= now.hour < 10:
                schedule = get_todays_schedule()
                if schedule and "расписание на сегодня" in schedule.lower():
                    suggestions.append("Сэр, показать расписание на сегодня?")

        except Exception:
            pass

        return suggestions
    
    def _get_smart_reminder_suggestions(self, context: Dict) -> List[str]:
        """
        Генерирует умные напоминания с эмоциональным интеллектом
        
        Args:
            context: Текущий контекст
            
        Returns:
            Список умных напоминаний
        """
        suggestions = []
        
        try:
            from actions.calendar import get_events
            
            # Получаем события на сегодня
            events_data = get_events("today")
            if not events_data or "событий" in events_data.lower():
                return suggestions
            
            # Парсим события (простой парсинг из строки)
            # В реальной реализации нужно улучшить парсинг
            current_context = {
                "emotion": context.get("last_emotion", "neutral"),
                "lifestyle_mode": context.get("mode", "normal"),
                "activity": context.get("current_activity", ""),
                "event_priority": "normal"
            }
            
            # Для каждого события генерируем умное напоминание
            # (упрощённая реализация - в реальности нужно парсить события из JSON)
            # Здесь просто добавим одно предложение для демонстрации
            if current_context["emotion"] in ["stressed", "overwhelmed"]:
                suggestions.append("Сэр, я вижу вы напряжены. Хотите отложить некоторые напоминания?")
            elif current_context["lifestyle_mode"] == "movie":
                suggestions.append("Сэр, у вас есть запланированные события. Приостановить фильм когда придёт время?")
            
        except Exception:
            pass
        
        return suggestions
    
    def _get_health_suggestions(self, context: Dict) -> List[str]:
        """
        Генерирует health-напоминания (фильм > 2 часов, работа > 1 час)
        
        Args:
            context: Текущий контекст
            
        Returns:
            Список health-напоминаний
        """
        suggestions = []
        
        try:
            # Проверяем health needs
            health_reminder = check_health_needs(context)
            if health_reminder:
                suggestions.append(health_reminder)
        except Exception:
            pass
        
        return suggestions


# Тестирование
if __name__ == "__main__":
    from pathlib import Path

    engine = ProactiveEngine(Path(__file__).parent.parent)

    # Тест записи действия
    engine.record_action("weather", {"time": "morning", "emotion": "neutral"})
    engine.record_action("weather", {"time": "morning", "emotion": "neutral"})
    engine.record_action("weather", {"time": "morning", "emotion": "neutral"})
    engine.record_action("weather", {"time": "morning", "emotion": "neutral"})

    # Тест прогноза
    context = {
        "current_activity": None,
        "mode": "normal",
        "last_emotion": "neutral"
    }

    prediction = engine.predict_need(context)
    print(f"Прогноз: {prediction}")

    suggestions = engine.get_proactive_suggestions(context)
    print(f"Предложения: {suggestions}")
