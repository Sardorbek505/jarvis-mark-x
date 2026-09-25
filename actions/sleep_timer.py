"""JARVIS Mark X — Умный таймер сна с подтверждением голосом и автовыключением.

Сценарий работы:
  1. Пользователь: «Через полчаса буду спать» / «Поставь таймер сна на 30 минут».
  2. Джарвис: Заводит таймер, подтверждает голосом («Таймер сна установлен на 30 минут, сэр»).
  3. По истечении 30 минут:
     - Джарвис спрашивает голосом:
       «Сэр, вы планировали лечь спать в это время. Могу ли я выключить компьютер?»
     - Запускается окно ожидания (_CONFIRM_SEC).
  4. Реакция:
     - Если пользователь говорит «нет / отмени / я ещё работаю» -> выключение отменяется.
     - Если пользователь говорит «да / выключай / спокойной ночи» -> компьютер выключается сразу.
     - Если пользователь молчит _CONFIRM_SEC (уснул) -> компьютер автоматически выключается.
"""

import logging
import platform
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger("jarvis-sleeptimer")
_OS = platform.system()

# Окно на ответ. Отсчёт идёт с момента отправки вопроса, а Джарвису ещё
# нужно его произнести (секунды 3-5 плюс задержка ответа): при 15 с на
# сам ответ человеку оставалось меньше десяти секунд.
_CONFIRM_SEC = 30.0

# speak() кладёт текст в Live-сессию от лица ПОЛЬЗОВАТЕЛЯ. Отдай туда голый
# вопрос «Могу ли я выключить компьютер?» — и модель ответит на него сама,
# вплоть до «да, выключайте». Поэтому в сессию идёт указание, что сказать.
_ASK_PROMPT = (
    "[СИСТЕМА: таймер сна истёк. Спроси пользователя одной фразой: "
    "«Сэр, вы планировали лечь спать. Могу я выключить компьютер?» "
    "Сам на вопрос не отвечай. Скажет «да» — вызови sleep_timer с "
    "action=\"confirm\", скажет «нет» — с action=\"cancel\".]"
)
_GOODBYE_PROMPT = (
    "[СИСТЕМА: компьютер выключается по таймеру сна. "
    "Коротко попрощайся: «Спокойной ночи, сэр».]"
)


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
        self._confirm_event = threading.Event()

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
        self._confirm_deadline = time.time() + _CONFIRM_SEC
        self._confirm_event.clear()

        if player:
            player.write_log(f"SYS: 🌙 Время сна наступило. Жду подтверждения ({_CONFIRM_SEC:.0f} сек)...")

        self._say(_ASK_PROMPT, player)

        # «нет» -> cancel_event, «да» -> confirm_event, молчание -> выключение.
        deadline = time.time() + _CONFIRM_SEC
        while time.time() < deadline:
            if self._cancel_event.wait(0.2):
                self._is_waiting_confirmation = False
                logger.info("SleepTimer: Выключение отменено пользователем.")
                return
            if self._confirm_event.is_set():
                break
        self._is_waiting_confirmation = False

        logger.info("SleepTimer: подтверждено или молчание %.0f с — выключаю ПК.", _CONFIRM_SEC)
        self._execute_shutdown(player)

    def confirm_shutdown(self) -> str:
        """Пользователь ответил «да» на вопрос о выключении."""
        if not self._is_waiting_confirmation:
            return "Выключение сейчас не ожидает подтверждения, сэр."
        self._confirm_event.set()
        return "Выключаю компьютер. Спокойной ночи, сэр."

    def _say(self, text: str, player=None):
        if self._bot_ref and hasattr(self._bot_ref, "speak"):
            self._bot_ref.speak(text)
        elif player and hasattr(player, "speak"):
            player.speak(text)

    def _execute_shutdown(self, player=None):
        """Выполняет реальное выключение компьютера."""
        if player:
            player.write_log("SYS: 🔌 Завершение работы компьютера (Таймер сна)")

        self._say(_GOODBYE_PROMPT, player)

        time.sleep(4.0)  # Даём Джарвису договорить фразу прощания

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
        (confirm — «да» на вопрос о выключении; раньше проваливался в set
        и молча перезаводил таймер на 30 минут)
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

    if action in ("confirm", "yes", "да", "подтвердить"):
        return sleep_timer_manager.confirm_shutdown()

    # Парсинг длительности
    minutes = 30.0  # Значение по умолчанию
    if duration is not None:
        try:
            minutes = float(duration)
        except (ValueError, TypeError):
            minutes = 30.0

    return sleep_timer_manager.start_timer(minutes, player=player, bot=bot)
