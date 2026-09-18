"""
Режим «говорю, пока держу клавишу».

ЗАЧЕМ
    Пробуждение голосом хорошо, пока в комнате тихо. На созвоне, при фильме
    или рядом с говорящими людьми оно превращается в лотерею: микрофон открыт
    всегда, и каждая чужая фраза — повод для ассистента встрять. Клавиша
    быстрее слова и не ослышится.

    Главное свойство — не удобство, а то, что **микрофон закрыт**, пока
    клавишу не держат. Кадры не просто игнорируются на сервере: они не
    уезжают из дома.

ПОЧЕМУ ОПРОС, А НЕ ПЕРЕХВАТ
    `RegisterHotKey`, на котором работает core/hotkey_manager, сообщает о
    НАЖАТИИ, а не о том, что клавишу держат. Для «пока держу» нужно состояние,
    и единственный способ прочитать его глобально в Windows без новых
    зависимостей — опрашивать `GetAsyncKeyState` тридцать раз в секунду. Это
    два вызова на кадр, около 60 в секунду; на фоне звукового потока в
    16 000 отсчётов в секунду — ничто.

ЧЕСТНО О ГРАНИЦАХ
    Глобально это работает только на Windows. На macOS и Linux прочитать
    состояние клавиш из чужого приложения без дополнительных прав и пакетов
    нельзя. Там режим доступен, только когда окно ДЖАРВИСА в фокусе, и модуль
    говорит об этом прямо, а не делает вид, что всё в порядке: молча
    неработающий PTT означает молчащий микрофон, и человек будет говорить в
    пустоту.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable

logger = logging.getLogger("jarvis.ptt")

# Виртуальные коды клавиш Windows.
VK_CONTROL = 0x11
VK_SPACE = 0x20

# Тридцать опросов в секунду: на глаз мгновенно, по нагрузке незаметно.
POLL_HZ = 30

# Хвост после отпускания клавиши. Без него обрывается последний слог: человек
# отпускает клавишу ровно на последнем звуке, а звук идёт блоками по 30–60 мс.
RELEASE_TAIL_SEC = 0.35


class PushToTalk:
    """Следит за аккордом и сообщает, зажат он или нет.

    `on_change(держат: bool)` зовётся ТОЛЬКО при смене состояния — из
    опрашивающего потока, поэтому обработчик должен быть коротким и
    потокобезопасным."""

    def __init__(self, on_change: Callable[[bool], None] | None = None):
        self._on_change = on_change
        self._thread: threading.Thread | None = None
        self._running = False
        self._held = False
        self._released_at = 0.0
        self._lock = threading.Lock()

    # ── Состояние ─────────────────────────────────────────────────────────────

    @property
    def held(self) -> bool:
        with self._lock:
            return self._held

    def mic_open(self) -> bool:
        """Можно ли сейчас пропускать кадры микрофона.

        Держат клавишу — да. Отпустили только что — ещё да, пока не истёк
        хвост: иначе фраза обрывается на последнем слоге."""
        with self._lock:
            if self._held:
                return True
            return (time.monotonic() - self._released_at) < RELEASE_TAIL_SEC

    @staticmethod
    def global_capable() -> bool:
        """Работает ли режим поверх чужих окон."""
        return sys.platform == "win32"

    @staticmethod
    def scope_note() -> str:
        """Строка для лога — человек должен знать, чего ждать."""
        if PushToTalk.global_capable():
            return "Ctrl+Space работает из любого приложения"
        return (f"Ctrl+Space работает только когда окно ДЖАРВИСА в фокусе: "
                f"на {sys.platform} состояние клавиш из чужих окон не прочитать")

    # ── Жизненный цикл ────────────────────────────────────────────────────────

    def start(self) -> bool:
        """True — опрос запущен. False — на этой системе его нет, и клавишу
        придётся ловить окну (см. `set_held`)."""
        if not self.global_capable():
            logger.info("PTT: %s", self.scope_note())
            return False
        if self._thread and self._thread.is_alive():
            return True

        self._running = True
        self._thread = threading.Thread(target=self._loop, name="PushToTalk", daemon=True)
        self._thread.start()
        logger.info("PTT: %s", self.scope_note())
        return True

    def stop(self) -> None:
        self._running = False
        поток, self._thread = self._thread, None
        if поток and поток.is_alive():
            поток.join(timeout=1.0)
        # Отпускаем «зажатое» состояние: иначе выключенный режим оставил бы
        # микрофон открытым навсегда.
        self._set_held(False)

    def set_held(self, держат: bool) -> None:
        """Внешний источник состояния — для macOS и Linux, где клавишу ловит
        окно, а не опрос."""
        self._set_held(bool(держат))

    # ── Внутреннее ────────────────────────────────────────────────────────────

    def _set_held(self, держат: bool) -> None:
        with self._lock:
            if держат == self._held:
                return
            self._held = держат
            if not держат:
                self._released_at = time.monotonic()

        if self._on_change:
            try:
                self._on_change(держат)
            except Exception as exc:
                logger.warning("Обработчик PTT сорвался: %s", exc)

    def _loop(self) -> None:
        import ctypes

        user32 = ctypes.windll.user32
        шаг = 1.0 / POLL_HZ

        while self._running:
            try:
                # Старший бит — «клавиша нажата сейчас». Младший означает
                # «была нажата с прошлого опроса» и для удержания не годится.
                ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                space = user32.GetAsyncKeyState(VK_SPACE) & 0x8000
                self._set_held(bool(ctrl and space))
            except Exception as exc:
                logger.warning("Опрос клавиш прекращён: %s", exc)
                return
            time.sleep(шаг)


# ─── Настройка ────────────────────────────────────────────────────────────────

def enabled() -> bool:
    """Включён ли режим. Хранится там же, где остальные настройки."""
    try:
        from core.paths import load_api_keys
        return bool(load_api_keys().get("push_to_talk", False))
    except Exception as exc:
        logger.debug("Настройка PTT не прочиталась: %s", exc)
        return False


def set_enabled(значение: bool) -> bool:
    try:
        from core.paths import load_api_keys, save_api_keys
        данные = load_api_keys()
        данные["push_to_talk"] = bool(значение)
        save_api_keys(данные)
        return True
    except Exception as exc:
        logger.warning("Настройка PTT не сохранена: %s", exc)
        return False
