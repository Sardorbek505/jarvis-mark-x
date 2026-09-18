"""
Подтверждение необратимых действий, которое модель не может подделать.

ЧТО БЫЛО НЕ ТАК
    Гейт в `main.py._execute_tool` считал подтверждением ПОВТОРНЫЙ вызов того
    же инструмента в течение 90 секунд. Модели было велено переспросить
    пользователя вслух — но ничто не проверяло, что человек вообще ответил.
    Два вызова подряд модель делает сама, без единого слова от пользователя,
    и компьютер выключается.

    Вдобавок поле `self._pending_destructive` нигде не инициализировалось, так
    что первое же «выключи компьютер» падало с AttributeError прямо в приёмном
    цикле и роняло сессию.

КАК УСТРОЕНО ЗДЕСЬ
    Токен подтверждения выдаёт ИНТЕРФЕЙС, а не модель:

      1. Инструмент, помеченный как необратимый, вызывает `request(...)`.
         Функция показывает баннер в HUD и СРАЗУ возвращает строку-инструкцию
         для модели. Ничего не блокируется: Джарвис продолжает говорить.
      2. Действие не выполняется. Модель говорит вслух, что ждёт подтверждения.
      3. Если — и только если — человек нажал ПОДТВЕРДИТЬ, Qt зовёт
         `resolve(True)`. Здесь выдаётся токен на этот конкретный ключ и
         в сессию уходит текст «пользователь подтвердил, выполняй».
      4. Модель вызывает инструмент повторно, `consume(key)` съедает токен,
         действие выполняется.

    Токен нельзя создать из модели: единственный путь к `resolve` — нажатие
    кнопки в окне.

ЧТО СЮДА НЕ ОТНОСИТСЯ
    Только по-настоящему необратимое. Всё, что откатывается, надо делать сразу
    и класть в стек `core/undo.py` — отмена быстрее вопроса, а ассистент,
    переспрашивающий на каждую громкость, — это ассистент, с которым перестают
    разговаривать.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger("jarvis.confirm")

# Сколько живёт баннер, если на него не ответили. Пережить паузу «секунду,
# гляну на экран» и не оставить кнопку выключения висеть до вечера.
PENDING_TTL_SEC = 90.0

# Сколько живёт выданный токен. Модели нужно услышать системную реплику и
# вызвать инструмент заново — это секунды, но сеть бывает медленной.
TOKEN_TTL_SEC = 120.0


@dataclass
class _Pending:
    key:   str
    title: str
    at:    float


_pending: _Pending | None = None
_granted: dict[str, float] = {}      # ключ → момент выдачи токена
_lock = threading.Lock()

_show_cb:   Callable[[str, str], None] | None = None
_hide_cb:   Callable[[], None] | None = None
_notify_cb: Callable[[str], None] | None = None
_log_cb:    Callable[[str], None] | None = None


def bind(show, hide, notify=None, log=None) -> None:
    """Подключает модуль к интерфейсу. Зовётся один раз при старте из main.py.

    show(title, detail) — показать баннер, hide() — убрать. Обе должны быть
    безопасны для вызова из чужого потока (в ui.py это pyqtSignal).
    notify(text) — отправить текст в Live-сессию, чтобы модель узнала о
    решении человека."""
    global _show_cb, _hide_cb, _notify_cb, _log_cb
    _show_cb, _hide_cb, _notify_cb, _log_cb = show, hide, notify, log


def available() -> bool:
    """False в headless-режиме и до привязки интерфейса. Вызывающий код тогда
    обязан не выполнять действие, а не выполнять его без спроса."""
    return _show_cb is not None


def _log(msg: str) -> None:
    if _log_cb:
        try:
            _log_cb(msg)
        except Exception as exc:
            logger.debug("Строка не дошла до лога интерфейса: %s", exc)


def _purge_locked(now: float) -> None:
    global _pending
    if _pending and now - _pending.at > PENDING_TTL_SEC:
        _pending = None
    for key in [k for k, at in _granted.items() if now - at > TOKEN_TTL_SEC]:
        _granted.pop(key, None)


def request(key: str, title: str, detail: str = "") -> str:
    """Ставит необратимое действие за экранный гейт.

    Возвращает строку для модели — сформулированную как инструкция, чтобы
    ассистент переспросил вслух на языке пользователя, а не зачитал наш текст
    дословно. Пустая строка означает «интерфейс не поднят»: действие не
    выполнено и подтвердить его сейчас негде."""
    global _pending

    if not available():
        return ""

    now = time.time()
    with _lock:
        _purge_locked(now)
        # Второй баннер поверх первого — верный способ подтвердить не то.
        if _pending and _pending.key != key:
            busy = _pending.title
            return (
                f"[ПОДТВЕРЖДЕНИЕ ЗАНЯТО] На экране уже висит подтверждение: {busy}. "
                f"Скажи пользователю одной фразой, что сначала нужно ответить на него. "
                f"Не утверждай, что выполнил «{title}»."
            )
        _pending = _Pending(key=key, title=title, at=now)

    try:
        _show_cb(title, detail)
    except Exception as exc:
        with _lock:
            _pending = None
        logger.warning("Не удалось показать подтверждение: %s", exc)
        return ""

    _log(f"SYS: жду подтверждения на экране — {title}")
    return (
        f"[ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ] Я показал на экране подтверждение: {title}. "
        f"Действие НЕ выполнено. Скажи пользователю ОДНУ короткую фразу на его "
        f"языке о том, что ждёшь нажатия кнопки на экране. Не заявляй, что "
        f"сделал это, и не вызывай инструмент повторно сам."
    )


def resolve(accepted: bool) -> None:
    """Зовётся интерфейсом, когда человек нажал ПОДТВЕРДИТЬ или ОТМЕНА."""
    global _pending

    now = time.time()
    with _lock:
        _purge_locked(now)
        pending, _pending = _pending, None
        if pending and accepted:
            _granted[pending.key] = now

    if _hide_cb:
        try:
            _hide_cb()
        except Exception as exc:
            # Баннер не убрался — решение всё равно принято, идём дальше.
            logger.warning("Не удалось убрать баннер подтверждения: %s", exc)

    if pending is None:
        return

    if accepted:
        _log(f"SYS: подтверждено — {pending.title}")
        _notify(
            f"[СИСТЕМА] Пользователь нажал ПОДТВЕРДИТЬ на экране для действия: "
            f"{pending.title}. Выполни его сейчас — вызови тот же инструмент "
            f"с теми же параметрами ещё раз."
        )
    else:
        _log(f"SYS: отменено — {pending.title}")
        _notify(
            f"[СИСТЕМА] Пользователь нажал ОТМЕНА для действия: {pending.title}. "
            f"Не выполняй его. Коротко подтверди вслух, что отменил."
        )


def _notify(text: str) -> None:
    if _notify_cb:
        try:
            _notify_cb(text)
        except Exception as exc:
            logger.debug("Не доставил решение в сессию: %s", exc)


def consume(key: str) -> bool:
    """Съедает токен, если человек его выдал. Одноразово: каждое необратимое
    действие требует своего нажатия."""
    now = time.time()
    with _lock:
        _purge_locked(now)
        at = _granted.pop(key, None)
    return at is not None


def pending_title() -> str:
    """Заголовок ожидающего подтверждения, либо пустая строка."""
    now = time.time()
    with _lock:
        _purge_locked(now)
        return _pending.title if _pending else ""


def reset() -> None:
    """Сброс состояния. Нужен тестам и закрытию приложения."""
    global _pending
    with _lock:
        _pending = None
        _granted.clear()
