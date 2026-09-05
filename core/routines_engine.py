"""JARVIS Mark X — Движок умных контекстных сценариев и макросов (Routines Engine).

Предоставляет многошаговые композитные сценарии автоматизации:
  1. morning   — «Доброе утро»: персональный утренний брифинг (время, дата, погода, календарь, бодрящая музыка).
  2. work      — «Я за работу»: запуск рабочего окружения (VS Code, Figma, Telegram) + фокусная музыка.
  3. movie     — «Режим кинотеатра»: сворачивание интерфейса в плавающий виджет Arc Reactor + запуск плеера.
  4. bedtime   — «Спокойной ночи»: пауза медиа, выключение/блокировка монитора, тихий режим.
  5. custom    — выполнение пользовательских макросов из config/routines.json.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("jarvis-routines")

_BASE = Path(__file__).resolve().parent.parent
_ROUTINES_CONFIG = _BASE / "config" / "routines.json"

_DAYS_RU = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье"
]

_MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]


class RoutinesEngine:
    """Исполнитель композитных автоматизированных сценариев (Routines)."""

    @classmethod
    def _load_custom_routines(cls) -> Dict[str, Any]:
        if _ROUTINES_CONFIG.exists():
            try:
                with open(_ROUTINES_CONFIG, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug("Error loading routines.json: %s", e)
        return {}

    @classmethod
    def execute(cls, routine_name: str, player=None, **kwargs) -> str:
        """
        Главная точка входа для исполнения сценария по имени.
        """
        name = (routine_name or "").strip().lower()
        logger.info("RoutinesEngine: ⚡ Запуск сценария «%s»", name)

        if name in ("morning", "утро", "доброе утро", "брифинг"):
            return cls._routine_morning(player=player)
        elif name in ("work", "работа", "я за работу", "рабочий режим"):
            return cls._routine_work(player=player)
        elif name in ("movie", "кино", "режим кинотеатра", "кинотеатр"):
            return cls._routine_movie(player=player)
        elif name in ("bedtime", "night", "сон", "спокойной ночи", "я спать", "отбой"):
            return cls._routine_bedtime(player=player)

        # Проверка пользовательских макросов из config/routines.json
        custom = cls._load_custom_routines()
        if name in custom:
            return cls._execute_custom(name, custom[name], player=player)

        return f"Сценарий «{routine_name}» не найден, сэр. Доступны: доброе утро, я за работу, режим кинотеатра, спокойной ночи."

    @classmethod
    def _routine_morning(cls, player=None) -> str:
        """Сценарий «Доброе утро»: брифинг (время, дата, погода, календарь, музыка)."""
        now = datetime.now()
        day_ru = _DAYS_RU[now.weekday()]
        month_ru = _MONTHS_RU[now.month]
        date_str = f"Сегодня {day_ru}, {now.day} {month_ru}, время {now.strftime('%H:%M')}."

        # 1. Погода
        weather_str = ""
        try:
            from actions.weather import weather_action
            weather_res = weather_action({"city": "Ташкент"}, player=player)
            if weather_res and "не удалось" not in weather_res.lower():
                weather_str = weather_res
        except Exception as e:
            logger.debug("Morning weather error: %s", e)

        # 2. Календарь и задачи на день
        schedule_str = ""
        try:
            from actions.calendar import get_todays_schedule
            sched = get_todays_schedule()
            if sched and "нет запланированных" not in sched.lower():
                schedule_str = f"Планы на день: {sched}."
        except Exception as e:
            logger.debug("Morning calendar error: %s", e)

        # 3. Фоновая бодрая музыка
        music_started = False
        try:
            from actions.music_player import music_player
            music_res = music_player({"action": "play", "query": "morning motivation chill"}, player=player)
            music_started = "не удалось" not in (music_res or "").lower()
        except Exception as e:
            logger.debug("Morning music error: %s", e)

        parts = [f"Доброе утро, сэр! {date_str}"]
        if weather_str:
            parts.append(weather_str)
        if schedule_str:
            parts.append(schedule_str)
        else:
            parts.append("Срочных задач на первую половину дня нет.")
        if music_started:
            parts.append("Включил утренний плейлист для хорошего настроения.")

        speech = " ".join(parts)
        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: 🌅 Сценарий «Доброе утро» выполнен")
        return speech

    @classmethod
    def _routine_work(cls, player=None) -> str:
        """Сценарий «Я за работу»: запуск рабочего софта и фокусной музыки."""
        try:
            from actions.modes import set_mode
            set_mode({"mode": "work", "preference": "code"}, player=player)
        except Exception as e:
            logger.debug("Work routine mode error: %s", e)

        # Запуск фокусного Lofi плейлиста
        try:
            from actions.music_player import music_player
            music_player({"action": "play", "query": "lofi hip hop focus study"}, player=player)
        except Exception as e:
            logger.debug("Work routine music error: %s", e)

        msg = "Рабочий режим активирован, сэр. Инструменты разработки развернуты, фоновая музыка запущена. Продуктивной работы!"
        if player and hasattr(player, "write_log"):
            player.write_log("SYS: 💼 Сценарий «Я за работу» выполнен")
        return msg

    @classmethod
    def _routine_movie(cls, player=None) -> str:
        """Сценарий «Режим кинотеатра»: сворачивание в Arc Reactor HUD + готовность плеера."""
        # 1. Сворачиваем интерфейс в виджет дугового реактора Тони Старка
        if player and hasattr(player, "set_compact_mode"):
            player.set_compact_mode(True)

        msg = "Режим кинотеатра активирован, сэр. Интерфейс свернулся в дуговой реактор, чтобы не загораживать экран. Приятного просмотра!"
        if player and hasattr(player, "write_log"):
            player.write_log("SYS: 🎬 Сценарий «Режим кинотеатра» выполнен")
        return msg

    @classmethod
    def _routine_bedtime(cls, player=None) -> str:
        """Сценарий «Спокойной ночи»: пауза медиа, выключение экрана, таймер сна."""
        # 1. Пауза всех медиаплееров
        try:
            from actions.music_player import _send_media_key
            _send_media_key("playpause")
        except Exception:
            pass

        # 2. Выключение / блокировка монитора
        try:
            from actions.computer_settings import computer_settings
            computer_settings({"action": "заблокировать экран"}, player=player)
        except Exception as e:
            logger.debug("Bedtime screen lock error: %s", e)

        # 3. Таймер сна (на случай если что-то осталось)
        try:
            from actions.sleep_timer import sleep_timer
            sleep_timer({"action": "set", "duration_minutes": 30}, player=player)
        except Exception:
            pass

        msg = "Доброй ночи, сэр. Экран выключен, воспроизведение остановлено. Отдыхайте, я продолжу мониторинг систем."
        if player and hasattr(player, "write_log"):
            player.write_log("SYS: 🌙 Сценарий «Спокойной ночи» выполнен")
        return msg

    @classmethod
    def _execute_custom(cls, name: str, routine_cfg: Dict[str, Any], player=None) -> str:
        """Выполнение шагов кастомного макроса."""
        steps = routine_cfg.get("steps", [])
        for step in steps:
            tool_name = step.get("tool")
            args = step.get("args", {})
            try:
                if tool_name == "music_player":
                    from actions.music_player import music_player
                    music_player(args, player=player)
                elif tool_name == "movie_player":
                    from actions.movie_player import movie_player
                    movie_player(args, player=player)
                elif tool_name == "computer_settings":
                    from actions.computer_settings import computer_settings
                    computer_settings(args, player=player)
                elif tool_name == "open_app":
                    from actions.open_app import open_app
                    open_app(args, player=player)
                elif tool_name == "browser_control":
                    from actions.browser_control import browser_control
                    browser_control(args, player=player)
            except Exception as e:
                logger.error("Custom macro step error (%s): %s", tool_name, e)

        resp = routine_cfg.get("response") or f"Макрос «{name}» успешно выполнен, сэр."
        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: ⚡ Макрос «{name}» выполнен")
        return resp
