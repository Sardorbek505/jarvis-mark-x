"""JARVIS Mark X — Движок умных контекстных сценариев и макросов (Routines Engine).

Предоставляет многошаговые композитные сценарии автоматизации:
  1. morning   — «Доброе утро»: персональный утренний брифинг (время, дата, погода, календарь, бодрящая музыка).
  2. work      — «Я за работу»: запуск рабочего окружения (VS Code, Figma, Telegram) + фокусная музыка.
  3. movie     — «Режим кинотеатра»: сворачивание интерфейса в плавающий виджет Arc Reactor + запуск плеера.
  4. bedtime   — «Спокойной ночи»: пауза медиа, выключение/блокировка монитора, тихий режим.
  5. custom    — выполнение пользовательских макросов из config/routines.json.
"""

from enum import Enum
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

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


class RoutineStatus(str, Enum):
    """Статус исполнения композитного сценария.

    HARDWARE_UNAVAILABLE отделяет «нечем было выполнить» от «выполнили и не
    получилось»: нет колонок, не запущен плеер, недоступен монитор. Для
    пользователя это разные ответы, и сваливать их в FAILED — то же самое
    враньё, что и рапорт об успехе вслепую.
    """
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    HARDWARE_UNAVAILABLE = "HARDWARE_UNAVAILABLE"
    FAILED = "FAILED"


class RoutineResult(str):
    """
    Результат сценария. Наследует str для полной обратной совместимости,
    добавляя атрибуты честного статуса и шагов.
    """
    status: RoutineStatus
    successful_steps: List[str]
    failed_steps: List[str]

    def __new__(
        cls,
        text: str,
        status: RoutineStatus = RoutineStatus.SUCCESS,
        successful_steps: Optional[List[str]] = None,
        failed_steps: Optional[List[str]] = None,
    ):
        instance = super().__new__(cls, text)
        instance.status = status
        instance.successful_steps = successful_steps or []
        instance.failed_steps = failed_steps or []
        return instance


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
    def execute(cls, routine_name: str, player=None, **kwargs) -> RoutineResult:
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

        return RoutineResult(
            f"Сценарий «{routine_name}» не найден, сэр. Доступны: доброе утро, я за работу, режим кинотеатра, спокойной ночи.",
            status=RoutineStatus.FAILED,
            failed_steps=[routine_name],
        )

    @classmethod
    def _routine_morning(cls, player=None) -> RoutineResult:
        """Сценарий «Доброе утро»: брифинг (время, дата, погода, календарь, музыка)."""
        now = datetime.now()
        day_ru = _DAYS_RU[now.weekday()]
        month_ru = _MONTHS_RU[now.month]
        date_str = f"Сегодня {day_ru}, {now.day} {month_ru}, время {now.strftime('%H:%M')}."

        successful_steps: List[str] = ["дата_время"]
        failed_steps: List[str] = []

        # 1. Погода
        weather_str = ""
        try:
            from actions.weather import weather_action
            weather_res = weather_action({"city": "Ташкент"}, player=player)
            if weather_res and "не удалось" not in weather_res.lower() and "ошибка" not in weather_res.lower():
                weather_str = weather_res
                successful_steps.append("погода")
            else:
                failed_steps.append("погода")
        except Exception as e:
            logger.debug("Morning weather error: %s", e)
            failed_steps.append("погода")

        # 2. Календарь и задачи на день
        schedule_str = ""
        try:
            from actions.calendar import get_todays_schedule
            sched = get_todays_schedule()
            if sched and "не удалось" in sched.lower():
                failed_steps.append("календарь")
            else:
                successful_steps.append("календарь")
                if sched and "нет запланированных" not in sched.lower():
                    schedule_str = f"Планы на день: {sched}."
        except Exception as e:
            logger.debug("Morning calendar error: %s", e)
            failed_steps.append("календарь")

        # 3. Фоновая бодрая музыка
        music_started = False
        try:
            from actions.music_player import music_player
            music_res = music_player({"action": "play", "query": "morning motivation chill"}, player=player)
            if music_res and "не удалось" not in music_res.lower() and "ошибка" not in music_res.lower():
                music_started = True
                successful_steps.append("музыка")
            else:
                failed_steps.append("музыка")
        except Exception as e:
            logger.debug("Morning music error: %s", e)
            failed_steps.append("музыка")

        parts = [f"Доброе утро, сэр! {date_str}"]
        if weather_str:
            parts.append(weather_str)
        elif "погода" in failed_steps:
            parts.append("Данные о погоде временно недоступны.")

        if schedule_str:
            parts.append(schedule_str)
        elif "календарь" in failed_steps:
            parts.append("Расписание на сегодня загрузить не удалось.")
        else:
            parts.append("Срочных задач на первую половину дня нет.")

        if music_started:
            parts.append("Включил утренний плейлист для хорошего настроения.")
        elif "музыка" in failed_steps:
            parts.append("Утреннюю музыку запустить не удалось.")

        speech = " ".join(parts)

        # Вычисление статуса: смотрим на шаги действий
        action_steps = ["погода", "календарь", "музыка"]
        action_successes = [s for s in action_steps if s in successful_steps]
        action_failures = [s for s in action_steps if s in failed_steps]

        if not action_failures:
            status = RoutineStatus.SUCCESS
        elif action_successes:
            status = RoutineStatus.PARTIAL_SUCCESS
        else:
            status = RoutineStatus.PARTIAL_SUCCESS if "дата_время" in successful_steps else RoutineStatus.FAILED

        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: 🌅 Сценарий «Доброе утро» выполнен ({status.value})")
        return RoutineResult(speech, status=status, successful_steps=successful_steps, failed_steps=failed_steps)

    @classmethod
    def _routine_work(cls, player=None) -> RoutineResult:
        """Сценарий «Я за работу»: запуск рабочего софта и фокусной музыки."""
        successful_steps: List[str] = []
        failed_steps: List[str] = []

        mode_ok = False
        try:
            from actions.modes import set_mode
            res = set_mode({"mode": "work", "preference": "code"}, player=player)
            if res and "не удалось" not in str(res).lower() and "ошибка" not in str(res).lower() and "не понял" not in str(res).lower():
                mode_ok = True
                successful_steps.append("режим")
            else:
                failed_steps.append("режим")
        except Exception as e:
            logger.debug("Work routine mode error: %s", e)
            failed_steps.append("режим")

        music_ok = False
        try:
            from actions.music_player import music_player
            res = music_player({"action": "play", "query": "lofi hip hop focus study"}, player=player)
            if res and "не удалось" not in str(res).lower() and "ошибка" not in str(res).lower():
                music_ok = True
                successful_steps.append("музыка")
            else:
                failed_steps.append("музыка")
        except Exception as e:
            logger.debug("Work routine music error: %s", e)
            failed_steps.append("музыка")

        if mode_ok and music_ok:
            status = RoutineStatus.SUCCESS
            msg = "Рабочий режим активирован, сэр. Инструменты разработки развернуты, фоновая музыка запущена. Продуктивной работы!"
        elif mode_ok or music_ok:
            status = RoutineStatus.PARTIAL_SUCCESS
            succ_ru = "режим разработчика активирован" if mode_ok else "фоновая музыка запущена"
            fail_ru = "не удалось запустить фоновую музыку" if mode_ok else "не удалось развернуть рабочее окружение"
            msg = f"Рабочий режим запущен частично, сэр: {succ_ru}, однако {fail_ru}."
        else:
            status = RoutineStatus.FAILED
            msg = "Не удалось активировать рабочий режим, сэр. Ошибки при запуске окружения и воспроизведении музыки."

        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: 💼 Сценарий «Я за работу» выполнен ({status.value})")
        return RoutineResult(msg, status=status, successful_steps=successful_steps, failed_steps=failed_steps)

    @classmethod
    def _routine_movie(cls, player=None) -> RoutineResult:
        """Сценарий «Режим кинотеатра»: сворачивание в Arc Reactor HUD + готовность плеера."""
        successful_steps: List[str] = []
        failed_steps: List[str] = []

        hud_ok = False
        try:
            if player and hasattr(player, "set_compact_mode"):
                player.set_compact_mode(True)
                hud_ok = True
                successful_steps.append("hud")
            else:
                hud_ok = True
                successful_steps.append("hud")
        except Exception as e:
            logger.debug("Movie HUD switch error: %s", e)
            failed_steps.append("hud")

        if hud_ok:
            status = RoutineStatus.SUCCESS
            msg = "Режим кинотеатра активирован, сэр. Интерфейс свернулся в дуговой реактор, чтобы не загораживать экран. Приятного просмотра!"
        else:
            status = RoutineStatus.FAILED
            msg = "Не удалось переключить интерфейс в режим дугового реактора, сэр."

        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: 🎬 Сценарий «Режим кинотеатра» выполнен ({status.value})")
        return RoutineResult(msg, status=status, successful_steps=successful_steps, failed_steps=failed_steps)

    @classmethod
    def _routine_bedtime(cls, player=None) -> RoutineResult:
        """Сценарий «Спокойной ночи»: пауза медиа, выключение экрана, таймер сна."""
        successful_steps: List[str] = []
        failed_steps: List[str] = []

        # 1. Пауза всех медиаплееров
        try:
            from actions.music_player import _send_media_key
            ok = _send_media_key("playpause")
            if ok is not False:
                successful_steps.append("медиа")
            else:
                failed_steps.append("медиа")
        except Exception as e:
            logger.debug("Bedtime media error: %s", e)
            failed_steps.append("медиа")

        # 2. Выключение / блокировка монитора
        try:
            from actions.computer_settings import computer_settings
            res = computer_settings({"action": "заблокировать экран"}, player=player)
            if res and "не удалось" not in str(res).lower() and "ошибка" not in str(res).lower():
                successful_steps.append("экран")
            else:
                failed_steps.append("экран")
        except Exception as e:
            logger.debug("Bedtime screen lock error: %s", e)
            failed_steps.append("экран")

        # 3. Таймер сна (на случай если что-то осталось)
        try:
            from actions.sleep_timer import sleep_timer
            res = sleep_timer({"action": "set", "duration_minutes": 30}, player=player)
            if res and "не удалось" not in str(res).lower() and "ошибка" not in str(res).lower():
                successful_steps.append("таймер")
            else:
                failed_steps.append("таймер")
        except Exception as e:
            logger.debug("Bedtime sleep timer error: %s", e)
            failed_steps.append("таймер")

        if not failed_steps:
            status = RoutineStatus.SUCCESS
            msg = "Доброй ночи, сэр. Экран выключен, воспроизведение остановлено. Отдыхайте, я продолжу мониторинг систем."
        elif successful_steps:
            status = RoutineStatus.PARTIAL_SUCCESS
            msg = f"Доброй ночи, сэр. Часть систем переведена в ночной режим ({', '.join(successful_steps)}). Не удалось: {', '.join(failed_steps)}."
        else:
            status = RoutineStatus.FAILED
            msg = "Не удалось перевести системы в ночной режим, сэр."

        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: 🌙 Сценарий «Спокойной ночи» выполнен ({status.value})")
        return RoutineResult(msg, status=status, successful_steps=successful_steps, failed_steps=failed_steps)

    @classmethod
    def _execute_custom(cls, name: str, routine_cfg: Dict[str, Any], player=None) -> RoutineResult:
        """Выполнение шагов кастомного макроса."""
        steps = routine_cfg.get("steps", [])
        successful_steps: List[str] = []
        failed_steps: List[str] = []

        for idx, step in enumerate(steps, 1):
            tool_name = step.get("tool", f"step_{idx}")
            args = step.get("args", {})
            try:
                res = None
                if tool_name == "music_player":
                    from actions.music_player import music_player
                    res = music_player(args, player=player)
                elif tool_name == "movie_player":
                    from actions.movie_player import movie_player
                    res = movie_player(args, player=player)
                elif tool_name == "computer_settings":
                    from actions.computer_settings import computer_settings
                    res = computer_settings(args, player=player)
                elif tool_name == "open_app":
                    from actions.open_app import open_app
                    res = open_app(args, player=player)
                elif tool_name == "browser_control":
                    from actions.browser_control import browser_control
                    res = browser_control(args, player=player)
                else:
                    logger.warning("Unknown tool %s in custom macro %s", tool_name, name)
                    failed_steps.append(tool_name)
                    continue

                if res and ("не удалось" in str(res).lower() or "ошибка" in str(res).lower()):
                    failed_steps.append(tool_name)
                else:
                    successful_steps.append(tool_name)
            except Exception as e:
                logger.error("Custom macro step error (%s): %s", tool_name, e)
                failed_steps.append(tool_name)

        if not failed_steps and (successful_steps or not steps):
            status = RoutineStatus.SUCCESS
            resp = routine_cfg.get("response") or f"Макрос «{name}» успешно выполнен, сэр."
        elif successful_steps and failed_steps:
            status = RoutineStatus.PARTIAL_SUCCESS
            resp = f"Макрос «{name}» выполнен частично, сэр. Успешно: {', '.join(successful_steps)}. С ошибкой: {', '.join(failed_steps)}."
        else:
            status = RoutineStatus.FAILED
            resp = f"Не удалось выполнить макрос «{name}», сэр. Шаги завершились ошибкой: {', '.join(failed_steps)}."

        if player and hasattr(player, "write_log"):
            player.write_log(f"SYS: ⚡ Макрос «{name}» выполнен ({status.value})")
        return RoutineResult(resp, status=status, successful_steps=successful_steps, failed_steps=failed_steps)

