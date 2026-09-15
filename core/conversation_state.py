"""JARVIS Mark X — Единая машина состояний диалога (ConversationStateMachine).

Центральный источник истины жизненного цикла ассистента.
Заменяет разрозненные булевы флаги (is_speaking, is_listening, tool_in_progress,
follow_up_active, interrupted_turn) на детерминированную машину состояний.
"""

from __future__ import annotations

import enum
import inspect
import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("jarvis-state")


class ConversationState(enum.Enum):
    STANDBY = "STANDBY"            # Ожидание wake word или хоткея
    LISTENING = "LISTENING"        # Запись речи пользователя
    THINKING = "THINKING"          # Ожидание ответа модели / роутера
    EXECUTING = "EXECUTING"        # Выполнение системного инструмента / макроса
    SPEAKING = "SPEAKING"          # Воспроизведение звукового ответа
    FOLLOW_UP = "FOLLOW_UP"        # Окно продолжения диалога без повтора имени
    INTERRUPTED = "INTERRUPTED"    # Перебивание (Barge-in)
    RECONNECTING = "RECONNECTING"  # Восстановление сети / WebSocket
    ERROR = "ERROR"                # Ошибка подсистемы
    MUTED = "MUTED"                # Микрофон отключён пользователем


StateListener = Callable[[ConversationState, ConversationState, str], None]


class ConversationStateMachine:
    """Потокобезопасная машина состояний диалога."""

    # Разрешённые детерминированные переходы.
    #
    # Инварианты, которые таблица действительно защищает:
    #   * из MUTED / ERROR / RECONNECTING нельзя вернуться в диалог минуя STANDBY;
    #   * FOLLOW_UP и INTERRUPTED недостижимы из STANDBY — продолжать и перебивать
    #     нечего, пока не было хода диалога.
    # Всё остальное внутри активного диалога разрешено: реальный рантайм
    # (fast-path, barge-in, tool-call, проактивная реплика) ходит между этими
    # состояниями в обе стороны, и отклонённый переход означал бы рассинхрон,
    # а не защиту.
    _ALLOWED_TRANSITIONS: Dict[ConversationState, Set[ConversationState]] = {
        ConversationState.STANDBY: {
            ConversationState.LISTENING,
            ConversationState.THINKING,
            ConversationState.RECONNECTING,
            ConversationState.MUTED,
            ConversationState.ERROR,
        },
        ConversationState.LISTENING: {
            ConversationState.THINKING,
            ConversationState.EXECUTING,
            ConversationState.SPEAKING,
            ConversationState.FOLLOW_UP,
            ConversationState.INTERRUPTED,
            ConversationState.STANDBY,
            ConversationState.MUTED,
            ConversationState.ERROR,
            ConversationState.RECONNECTING,
        },
        ConversationState.THINKING: {
            ConversationState.EXECUTING,
            ConversationState.SPEAKING,
            ConversationState.LISTENING,
            ConversationState.FOLLOW_UP,
            ConversationState.INTERRUPTED,
            ConversationState.STANDBY,
            ConversationState.MUTED,
            ConversationState.ERROR,
            ConversationState.RECONNECTING,
        },
        ConversationState.EXECUTING: {
            ConversationState.THINKING,
            ConversationState.SPEAKING,
            ConversationState.LISTENING,
            ConversationState.FOLLOW_UP,
            ConversationState.INTERRUPTED,
            ConversationState.STANDBY,
            ConversationState.MUTED,
            ConversationState.ERROR,
            ConversationState.RECONNECTING,
        },
        ConversationState.SPEAKING: {
            ConversationState.INTERRUPTED,
            ConversationState.FOLLOW_UP,
            ConversationState.LISTENING,
            ConversationState.THINKING,
            ConversationState.EXECUTING,
            ConversationState.STANDBY,
            ConversationState.MUTED,
            ConversationState.ERROR,
            ConversationState.RECONNECTING,
        },
        ConversationState.INTERRUPTED: {
            ConversationState.LISTENING,
            ConversationState.THINKING,
            ConversationState.SPEAKING,
            ConversationState.EXECUTING,
            ConversationState.STANDBY,
            ConversationState.MUTED,
            ConversationState.ERROR,
            ConversationState.RECONNECTING,
        },
        ConversationState.FOLLOW_UP: {
            ConversationState.LISTENING,
            ConversationState.THINKING,
            ConversationState.EXECUTING,
            ConversationState.SPEAKING,
            ConversationState.INTERRUPTED,
            ConversationState.STANDBY,
            ConversationState.MUTED,
            ConversationState.ERROR,
            ConversationState.RECONNECTING,
        },
        ConversationState.RECONNECTING: {
            ConversationState.STANDBY,
            ConversationState.MUTED,
            ConversationState.ERROR,
        },
        ConversationState.MUTED: {
            ConversationState.STANDBY,
            ConversationState.RECONNECTING,
            ConversationState.ERROR,
        },
        ConversationState.ERROR: {
            ConversationState.STANDBY,
            ConversationState.RECONNECTING,
            ConversationState.MUTED,
        },
    }

    def __init__(self, initial_state: ConversationState = ConversationState.STANDBY):
        self._lock = threading.RLock()
        self._state = initial_state
        self._listeners: List[Tuple[StateListener, bool]] = []
        self._follow_up_timer: Optional[threading.Timer] = None
        self._state_entered_at: float = time.monotonic()

    @property
    def state(self) -> ConversationState:
        with self._lock:
            return self._state

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._state == ConversationState.SPEAKING

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._state in (ConversationState.LISTENING, ConversationState.FOLLOW_UP)

    @property
    def is_active(self) -> bool:
        """Активна ли сессия диалога (не в режиме ожидания/отключения)."""
        with self._lock:
            return self._state not in (
                ConversationState.STANDBY,
                ConversationState.MUTED,
                ConversationState.ERROR,
                ConversationState.RECONNECTING,
            )

    @property
    def state_duration_sec(self) -> float:
        with self._lock:
            return time.monotonic() - self._state_entered_at

    @staticmethod
    def _listener_wants_reason(listener: StateListener) -> bool:
        """Принимает ли слушатель третий аргумент `reason`.

        Арность выясняется один раз при подписке. Раньше это делалось через
        `except TypeError` вокруг самого вызова — и слушатель, который сам
        бросал TypeError внутри, вызывался повторно с урезанной сигнатурой.
        """
        try:
            sig = inspect.signature(listener)
        except (TypeError, ValueError):
            return True
        positional = 0
        for param in sig.parameters.values():
            if param.kind is inspect.Parameter.VAR_POSITIONAL:
                return True
            if param.kind in (inspect.Parameter.POSITIONAL_ONLY,
                              inspect.Parameter.POSITIONAL_OR_KEYWORD):
                positional += 1
        return positional >= 3

    def subscribe(self, listener: StateListener) -> None:
        """Регистрирует подписчика на смену состояний."""
        wants_reason = self._listener_wants_reason(listener)
        with self._lock:
            if any(existing is listener for existing, _ in self._listeners):
                return
            self._listeners.append((listener, wants_reason))

    add_listener = subscribe

    def unsubscribe(self, listener: StateListener) -> None:
        with self._lock:
            self._listeners = [
                entry for entry in self._listeners if entry[0] is not listener
            ]

    def can_transition_to(self, target: ConversationState) -> bool:
        """Проверяет допустимость перехода в целевое состояние без вызова перехода."""
        with self._lock:
            allowed = self._ALLOWED_TRANSITIONS.get(self._state, set())
            return target in allowed

    def transition_to(
        self,
        new_state: ConversationState,
        reason: str = "",
        force: bool = False,
    ) -> bool:
        """Выполняет детерминированный переход в новое состояние."""
        with self._lock:
            old_state = self._state
            if old_state == new_state:
                return True

            allowed = self._ALLOWED_TRANSITIONS.get(old_state, set())
            if not force and new_state not in allowed:
                logger.warning(
                    "ConversationStateMachine: недопустимый переход %s -> %s (причина: '%s')",
                    old_state.value, new_state.value, reason,
                )
                return False

            # Отмена таймера Follow-Up при уходе из него
            if old_state == ConversationState.FOLLOW_UP and self._follow_up_timer:
                self._follow_up_timer.cancel()
                self._follow_up_timer = None

            self._state = new_state
            self._state_entered_at = time.monotonic()
            logger.info(
                "ConversationState: [%s -> %s] (%s)",
                old_state.value, new_state.value, reason or "unspecified",
            )
            listeners = list(self._listeners)

        # Оповещение слушателей строго вне лока: слушатели трогают UI и COM
        # (ducking), и вызов их под локом машины состояний — прямой путь к дедлоку.
        self._notify(listeners, old_state, new_state, reason)
        return True

    @staticmethod
    def _notify(listeners, old_state, new_state, reason: str) -> None:
        for listener, wants_reason in listeners:
            try:
                if wants_reason:
                    listener(old_state, new_state, reason)
                else:
                    listener(old_state, new_state)
            except Exception as e:
                logger.error("State listener error on %s -> %s: %s", old_state, new_state, e)

    def start_follow_up(self, timeout_sec: float = 4.5, on_timeout_reason: str = "follow-up timeout") -> bool:
        """Активирует состояние FOLLOW_UP с гарантированным запуском таймера.

        Возвращает True, если окно открыто. Таймер ставится ПОСЛЕ успешного
        перехода: иначе он мог бы сбросить в STANDBY состояние, в которое
        машина так и не перешла.
        """
        with self._lock:
            if self._follow_up_timer:
                self._follow_up_timer.cancel()
                self._follow_up_timer = None

        if not self.transition_to(
            ConversationState.FOLLOW_UP, reason=f"follow-up window {timeout_sec:.1f}s"
        ):
            return False

        def _on_timeout():
            if self.state == ConversationState.FOLLOW_UP:
                self.transition_to(ConversationState.STANDBY, reason=on_timeout_reason)

        with self._lock:
            # За время перехода состояние могло уйти дальше (пользователь заговорил) —
            # тогда таймер уже отменён в transition_to и вешать новый не нужно.
            if self._state is not ConversationState.FOLLOW_UP:
                return True
            timer = threading.Timer(timeout_sec, _on_timeout)
            timer.daemon = True
            self._follow_up_timer = timer
        timer.start()
        return True

    def reset_to_standby(self, reason: str = "reset") -> None:
        """Сброс машины состояний в дефолтное ожидание."""
        with self._lock:
            if self._follow_up_timer:
                self._follow_up_timer.cancel()
                self._follow_up_timer = None
        self.transition_to(ConversationState.STANDBY, reason=reason, force=True)
