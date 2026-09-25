"""JARVIS Mark X — Умный таймер сна с подтверждением голосом и автовыключением.

Сценарий работы:
  1. Пользователь: «Через полчаса буду спать» / «Поставь таймер сна на 30 минут».
  2. Джарвис: Заводит таймер, подтверждает голосом («Таймер сна установлен на 30 минут, сэр»).
  3. По истечении 30 минут:
     - Джарвис спрашивает голосом:
       «Сэр, вы планировали лечь спать в это время. Могу ли я выключить компьютер?»
     - Запускается 15-секундное окно ожидания.
  4. Реакция:
     - Если пользователь говорит «нет / отмени / я ещё работаю» -> выключение отменяется.
     - Если пользователь говорит «да / выключай / спокойной ночи» -> компьютер выключается сразу.
     - Если пользователь молчит 15 секунд (уснул) -> компьютер автоматически выключается.
"""

import logging
import platform
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger("jarvis-sleeptimer")
_OS = platform.system()


class SleepTimerManager:
    """Менеджер умного таймера сна с голосовым подтверждением и тайм-аутом молчания."""

    def __init__(self):
        self._timer_thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._target_time: Optional[float] = None
        self._duration_sec: float = 0.0
        self._is_waiting_confirmation = False
        self._confirm_deadline: float = 0.0
        self._bot_ref = None

    def set_bot_reference(self, bot):
        """Сохраняет ссылку на экземпляр JarvisBot для отправки голосовых фраз."""
        self._bot_ref = bot

    def is_active(self) -> bool:
        """Активен ли таймер сна прямо сейчас."""
        return (
            self._target_time is not None
            and not self._cancel_event.is_set()
            and self._timer_thread is not None
            and self._timer_thread.is_alive()
        )

    def get_remaining_seconds(self) -> float:
        """Сколько секунд осталось до срабатывания."""
        if not self._target_time or not self.is_active():
            return 0.0
        return max(0.0, self._target_time - time.time())

    def start_timer(self, minutes: float, player=None, bot=None) -> str:
        """Запускает таймер сна на указанное количество минут."""
        if bot:
            self._bot_ref = bot

        # Отменяем предыдущий таймер если был
        self.cancel_timer(player=None)

        if minutes <= 0:
            return "Укажите корректное время в минутах, сэр."

        self._duration_sec = minutes * 60.0
        self._target_time = time.time() + self._duration_sec
        self._cancel_event.clear()
        self._is_waiting_confirmation = False

        def _worker():
            logger.info("SleepTimer: Запущен на %.1f минут (до %s)", minutes, time.strftime('%H:%M:%S', time.localtime(self._target_time)))
            
            # Ждём завершения таймера
            if self._cancel_event.wait(self._duration_sec):
                logger.info("SleepTimer: Таймер отменён до срабатывания")
                return

            # Таймер истёк — переходим к фазе подтверждения
            self._on_timer_expired(player)

        self._timer_thread = threading.Thread(target=_worker, daemon=True, name="sleep-timer-worker")
        self._timer_thread.start()

        mins_int = int(minutes)
        if mins_int == minutes:
            time_str = f"{mins_int} мин."
        else:
            time_str = f"{minutes:.1f} мин."

        msg = f"Таймер сна установлен на {time_str}. Через {time_str} я проверю, спите ли вы, и выключу компьютер."
        if player:
            player.write_log(f"SYS: 🌙 Таймер сна на {time_str}")
        return msg

    def cancel_timer(self, player=None) -> str:
        """Отменяет активный таймер сна."""
        if not self.is_active() and not self._is_waiting_confirmation:
            return "Таймер сна не был установлен, сэр."

        self._cancel_event.set()
        self._is_waiting_confirmation = False
        self._target_time = None

        msg = "Таймер сна отменён, сэр. Компьютер останется включённым."
        if player:
            player.write_log("SYS: ✕ Таймер сна отменён")
        return msg

    def get_status(self) -> str:
        """Возвращает текущий статус таймера сна."""
        if not self.is_active():
            return "Таймер сна не установлен, сэр."

        rem_sec = int(self.get_remaining_seconds())
        rem_min = rem_sec // 60
        rem_sec_left = rem_sec % 60

        if rem_min > 0:
            return f"Таймер сна активен: осталось {rem_min} мин {rem_sec_left} сек."
        return f"Таймер сна активен: осталось {rem_sec_left} сек."

    def _on_timer_expired(self, player=None):
        """Срабатывает по истечении основного времени."""
        logger.info("SleepTimer: Время сна наступило. Запрос подтверждения у пользователя.")
        self._is_waiting_confirmation = True
        self._confirm_deadline = time.time() + 15.0

        ask_phrase = "Сэр, вы планировали лечь спать в это время. Могу ли я выключить компьютер?"
        
        if player:
            player.write_log("SYS: 🌙 Время сна наступило. Запрашиваю подтверждение выключения (15 сек)...")

        # Озвучиваем вопрос
        if self._bot_ref and hasattr(self._bot_ref, "speak"):
            self._bot_ref.speak(ask_phrase)
        elif player and hasattr(player, "speak"):
            player.speak(ask_phrase)

        # Ожидаем 15 секунд ответа
        # Если пользователь ответил "нет" / отменил -> cancel_event будет установлен
        # Если пользователь молчит 15 секунд -> выключаем компьютер
        cancelled = self._cancel_event.wait(15.0)
        self._is_waiting_confirmation = False

        if cancelled:
            logger.info("SleepTimer: Выключение отменено пользователем.")
            return

        # Пользователь промолчал (уснул) -> Выключаем компьютер
        logger.info("SleepTimer: 15 секунд молчания истекли. Выполняю выключение ПК.")
        self._execute_shutdown(player)

    def _execute_shutdown(self, player=None):
        """Выполняет реальное выключение компьютера."""
        goodbye_msg = "Спокойной ночи, сэр. Завершаю работу всех систем."
        
        if player:
            player.write_log("SYS: 🔌 Завершение работы компьютера (Таймер сна)")

        if self._bot_ref and hasattr(self._bot_ref, "speak"):
            self._bot_ref.speak(goodbye_msg)
        elif player and hasattr(player, "speak"):
            player.speak(goodbye_msg)

        time.sleep(2.0)  # Даём Джарвису договорить фразу прощания

        try:
            if _OS == "Windows":
                # Завершение работы Windows
                subprocess.run(["shutdown", "/s", "/t", "0"], capture_output=True)
            elif _OS == "Darwin":
                subprocess.run(["osascript", "-e", 'tell app "System Events" to shut down'])
            else:
                subprocess.run(["systemctl", "poweroff"])
        except Exception as e:
            logger.error("SleepTimer shutdown error: %s", e)


# Глобальный синглтон
sleep_timer_manager = SleepTimerManager()


def sleep_timer(parameters: dict, player=None, bot=None) -> str:
    """
    Главная точка входа для tool 'sleep_timer'.

    parameters:
        action: 'set' | 'cancel' | 'status' | 'confirm'
        duration_minutes: float (например, 30 или 0.5)
        text: исходный текст пользователя (для парсинга времени)
    """
    action = (parameters.get("action") or "set").strip().lower()
    duration = parameters.get("duration_minutes") or parameters.get("minutes") or parameters.get("duration")

    # Если действие — отмена
    if action in ("cancel", "отмена", "отменить", "стоп", "выключи_таймер"):
        return sleep_timer_manager.cancel_timer(player)

    # Если действие — статус
    if action in ("status", "статус", "сколько_осталось", "инфо"):
        return sleep_timer_manager.get_status()

    # Парсинг длительности
    minutes = 30.0  # Значение по умолчанию
    if duration is not None:
        try:
            minutes = float(duration)
        except (ValueError, TypeError):
            minutes = 30.0

    return sleep_timer_manager.start_timer(minutes, player=player, bot=bot)
