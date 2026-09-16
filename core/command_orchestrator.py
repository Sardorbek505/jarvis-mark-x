"""JARVIS Mark X — Оркестратор команд (Command Orchestrator).

Реализует:
  1. Строгий Routing Contract (исключение double execution и double TTS)
  2. Управление активным CommandContext и машиной состояний CommandState
  3. Контекстное слияние параметров (slot filling)
  4. Защиту асинхронных операций (turn_id / command_id / generation_id) от stale callbacks
  5. Разделение Command TTL (60 сек) и Follow-up окна микрофона (10-12 сек)
  6. Безопасную отмену во время выполнения (cancellation during execution) с ровно одним голосовым ответом
  7. Классификацию намерений без побочных эффектов (LLMIntentClassifier)
"""

from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from core.command_context import (
    INTENT_REGISTRY,
    LOCAL_ACTION_INTENTS,
    CommandContext,
    CommandState,
)
from core.intent_classifier import LLMIntentClassifier
from core.media_executor import MediaExecutor
from core.slot_filler import SlotFiller, normalize_text

logger = logging.getLogger("jarvis-orchestrator")


class RoutingDecision(str, enum.Enum):
    """Результат маршрутизации реплики в оркестраторе."""
    CONSUMED_ACTION = "CONSUMED_ACTION"                # Команда выполнена детерминированно
    CONSUMED_CLARIFICATION = "CONSUMED_CLARIFICATION"  # Требуется уточнение параметров (вопрос пользователю)
    CONSUMED_CANCEL = "CONSUMED_CANCEL"                # Активная команда отменена
    NEEDS_LLM = "NEEDS_LLM"                            # Не является детерминированным действием — передать в Gemini
    IGNORED = "IGNORED"                                # Шум / пусто / устаревший колбэк


@dataclass
class ProcessResult:
    """Результат обработки реплики оркестратором."""
    decision: RoutingDecision
    command_id: Optional[str] = None
    turn_id: int = 0
    prompt_to_user: Optional[str] = None
    executed_result: Optional[str] = None
    action_name: Optional[str] = None
    target_slot: Optional[str] = None
    cleaned_text: Optional[str] = None
    success: bool = True


class CommandOrchestrator:
    """Центральный диспетчер многошаговых и одношаговых команд."""

    def __init__(self, classifier: Optional[LLMIntentClassifier] = None):
        self._lock = threading.RLock()
        self._active_command: Optional[CommandContext] = None
        self._current_command_id: Optional[str] = None
        self._current_turn_id: int = 0
        self._executing_command_id: Optional[str] = None
        self._intent_classifier = classifier or LLMIntentClassifier()

    @property
    def active_command(self) -> Optional[CommandContext]:
        with self._lock:
            return self._active_command

    def has_active_command(self, now: Optional[float] = None) -> bool:
        with self._lock:
            if not self._active_command:
                return False
            if self._active_command.is_expired(now=now):
                logger.info("[COMMAND] TTL истёк для команды %s (%s)", self._active_command.id, self._active_command.intent)
                self._active_command = None
                return False
            return self._active_command.state in (
                CommandState.COLLECTING_PARAMETERS,
                CommandState.READY_TO_EXECUTE,
                CommandState.EXECUTING,
            )

    def is_stale_callback(self, command_id: Optional[str], turn_id: int) -> bool:
        """Проверяет, не устарел ли асинхронный колбэк (TTS, execution result)."""
        with self._lock:
            if not command_id or command_id != self._current_command_id:
                return True
            if turn_id != self._current_turn_id:
                return True
            return False

    def cancel_active_command(self, reason: str = "user_cancel") -> Tuple[bool, str]:
        """Отменяет активную команду, если она существует."""
        with self._lock:
            if not self._active_command:
                return False, "Нет активных команд для отмены."
            cmd_id = self._active_command.id
            self._active_command.state = CommandState.CANCELLED
            logger.info("[COMMAND] %s отменена (%s)", cmd_id, reason)
            self._active_command = None
            self._current_command_id = None
            self._current_turn_id = 0
            return True, "Команда отменена, сэр."

    def process_user_text(self, text: str, player=None) -> ProcessResult:
        """
        Главная точка входа для текстовых и распознанных голосовых команд.
        Строго соблюдает контракт маршрутизации (Single Ownership).
        """
        clean = normalize_text(text)
        if not clean:
            return ProcessResult(decision=RoutingDecision.IGNORED)

        with self._lock:
            # 1. Проверка истечения TTL активной команды
            if self._active_command and self._active_command.is_expired():
                logger.info("[COMMAND] TTL истёк для команды %s", self._active_command.id)
                self._active_command = None
                self._current_command_id = None
                self._current_turn_id = 0

            # 2. Проверка отмены или составной отмены («не надо, какая погода завтра»)
            is_cancel, remaining = SlotFiller.extract_interruption_or_cancellation(clean)
            if is_cancel:
                if self._active_command:
                    self.cancel_active_command(reason="user_requested_cancel")
                    if not remaining:
                        return ProcessResult(
                            decision=RoutingDecision.CONSUMED_CANCEL,
                            prompt_to_user="Отменено, сэр.",
                        )
                    clean = normalize_text(remaining)
                elif not remaining:
                    return ProcessResult(
                        decision=RoutingDecision.CONSUMED_CANCEL,
                        prompt_to_user="Никаких активных задач не выполняется, сэр.",
                    )
                else:
                    clean = normalize_text(remaining)

            # 3. Обработка реплики относительно АКТИВНОЙ команды
            if self._active_command and self._active_command.state == CommandState.COLLECTING_PARAMETERS:
                cmd = self._active_command
                target_slot = cmd.target_slot

                # Проверяем, не является ли реплика явным НОВЫМ интентом (прерывание)
                new_intent_probe = SlotFiller.detect_new_intent(clean)
                if new_intent_probe and new_intent_probe[0] != cmd.intent:
                    logger.info("[COMMAND] Прерывание новой локальной командой: %s -> %s", cmd.intent, new_intent_probe[0])
                    cmd.state = CommandState.CANCELLED
                    self._active_command = None
                else:
                    # Контекстное извлечение слотов
                    contextual_slots = SlotFiller.extract_contextual_slots(clean, target_slot=target_slot)
                    if not contextual_slots and target_slot == "title":
                        contextual_slots = {"title": clean.capitalize()}

                    if contextual_slots:
                        turn_id = cmd.next_turn()
                        self._current_turn_id = turn_id
                        updated = cmd.merge_slots(contextual_slots)
                        for u in updated:
                            logger.info("[SLOT_MERGE] %s=%s", u, cmd.slots.get(u))

                        return self._evaluate_command_readiness(cmd, player=player)

            # 4. Обнаружение НОВОГО интента (если нет активной команды или была отменена)
            detected = SlotFiller.detect_new_intent(clean)
            if not detected:
                # Пробуем fallback классификатора без побочных эффектов для естественных формулировок
                clf_res = self._intent_classifier.classify_intent_sync(clean)
                if clf_res.get("intent") in LOCAL_ACTION_INTENTS and clf_res.get("confidence", 0) >= 0.7:
                    detected = (clf_res["intent"], clf_res.get("slots", {}))

            if detected:
                intent_name, initial_slots = detected
                intent_def = INTENT_REGISTRY.get(intent_name)
                if intent_def:
                    cmd = CommandContext(
                        intent=intent_name,
                        slots=initial_slots,
                        required_slots=list(intent_def.required_slots),
                        optional_slots=list(intent_def.optional_slots),
                    )
                    cmd.update_missing_slots()
                    self._active_command = cmd
                    self._current_command_id = cmd.id
                    self._current_turn_id = cmd.turn_id

                    logger.info("[COMMAND] Создана команда intent=%s, id=%s, slots=%s", intent_name, cmd.id, cmd.slots)
                    return self._evaluate_command_readiness(cmd, player=player)

            # 5. Если интент не опознан, но активная команда ждёт название
            if self._active_command and self._active_command.state == CommandState.COLLECTING_PARAMETERS and self._active_command.target_slot == "title":
                cmd = self._active_command
                turn_id = cmd.next_turn()
                self._current_turn_id = turn_id
                cmd.merge_slots({"title": clean})
                logger.info("[SLOT_MERGE] title=%s", clean)
                return self._evaluate_command_readiness(cmd, player=player)

            # 6. Не относится к локальным действиям — передаём в Gemini как диалоговый запрос
            return ProcessResult(decision=RoutingDecision.NEEDS_LLM, cleaned_text=clean)

    def _evaluate_command_readiness(self, cmd: CommandContext, player=None) -> ProcessResult:
        """Оценивает готовность команды к выполнению или формулирует уточняющий вопрос."""
        if cmd.missing_slots:
            cmd.state = CommandState.COLLECTING_PARAMETERS
            missing_slot = cmd.missing_slots[0]
            cmd.target_slot = missing_slot

            intent_def = INTENT_REGISTRY.get(cmd.intent)
            prompt = "Уточните параметр, сэр."
            if intent_def and missing_slot in intent_def.slot_defs:
                prompt = intent_def.slot_defs[missing_slot].prompt_question

            if cmd.intent == "play_media":
                if missing_slot == "title":
                    media_type = cmd.slots.get("media_type")
                    if media_type == "series":
                        prompt = "Какой сериал включить, сэр?"
                    elif media_type == "movie":
                        prompt = "Какой фильм включить, сэр?"
                    else:
                        prompt = "Что именно включить, сэр?"

            cmd.last_prompt = prompt
            logger.info("[STATE] intent=%s, state=%s, target_slot=%s", cmd.intent, cmd.state.value, cmd.target_slot)
            return ProcessResult(
                decision=RoutingDecision.CONSUMED_CLARIFICATION,
                command_id=cmd.id,
                turn_id=cmd.turn_id,
                prompt_to_user=prompt,
                target_slot=missing_slot,
            )

        if cmd.intent == "play_media" and cmd.slots.get("media_type") == "series" and cmd.slots.get("season") is None and getattr(cmd, "_season_asked", False) is False:
            cmd._season_asked = True
            cmd.state = CommandState.COLLECTING_PARAMETERS
            cmd.target_slot = "season"
            prompt = "Какой сезон, сэр?"
            cmd.last_prompt = prompt
            logger.info("[STATE] intent=%s, state=%s, target_slot=season", cmd.intent, cmd.state.value)
            return ProcessResult(
                decision=RoutingDecision.CONSUMED_CLARIFICATION,
                command_id=cmd.id,
                turn_id=cmd.turn_id,
                prompt_to_user=prompt,
                target_slot="season",
            )

        cmd.state = CommandState.READY_TO_EXECUTE
        logger.info("[STATE] %s -> READY_TO_EXECUTE", cmd.intent)

        return self._execute_command(cmd, player=player)

    def _execute_command(self, cmd: CommandContext, player=None) -> ProcessResult:
        """Детерминированное исполнение готовой команды."""
        with self._lock:
            if cmd.state == CommandState.CANCELLED or (self._executing_command_id and self._executing_command_id != cmd.id):
                logger.warning("[EXECUTOR] Попытка запуска отменённой или устаревшей команды %s", cmd.id)
                self._executing_command_id = None
                return ProcessResult(
                    decision=RoutingDecision.IGNORED,
                    command_id=cmd.id,
                    turn_id=cmd.turn_id,
                    prompt_to_user=None,
                )
            cmd.state = CommandState.EXECUTING
            self._executing_command_id = cmd.id
            logger.info("[STATE] %s -> EXECUTING (id=%s)", cmd.intent, cmd.id)

        result_msg = "Команда выполнена, сэр."
        success = True

        try:
            if cmd.intent == "play_media":
                success, result_msg = MediaExecutor.execute(cmd.slots, player=player)

            elif cmd.intent == "open_app":
                from actions.open_app import open_app
                app_name = cmd.slots.get("app_name", "")
                r = open_app({"app_name": app_name}, player=player)
                result_msg = r or f"Открыл {app_name}, сэр."

            elif cmd.intent == "play_music":
                from actions.spotify_controller import spotify_player
                from core.media.orchestrator import get_media_orchestrator
                query = cmd.slots.get("query", "")
                media = get_media_orchestrator()
                # Играющее видео — на паузу ДО запуска музыки, а не после
                media.preempt_active_session()
                r = spotify_player({"action": "play", "query": query}, player=player)
                result_msg = r or f"Включаю музыку «{query}», сэр."
                failed = any(w in result_msg.lower() for w in ("не удалось", "недоступен", "ошибка", "не найден"))
                if not failed:
                    from core.media.controllers.spotify import SpotifyMediaController
                    from core.media.models import MediaType
                    media.register_app_session(MediaType.MUSIC, query or "Spotify", "spotify", SpotifyMediaController())
                else:
                    success = False

            elif cmd.intent == "sleep_timer":
                from actions.sleep_timer import sleep_timer
                r = sleep_timer(cmd.slots, player=player)
                result_msg = r or "Таймер сна обновлён, сэр."

        except Exception as e:
            logger.error("[EXECUTOR] Исключение при выполнении %s: %s", cmd.intent, e)
            success = False
            result_msg = f"Произошла ошибка при выполнении команды: {e}"

        with self._lock:
            # Если пользователь отменил команду во время исполнения:
            # Возвращаем IGNORED без secondary TTS (пользователю уже было сказано об отмене)
            if self._executing_command_id != cmd.id or cmd.state == CommandState.CANCELLED:
                logger.warning("[EXECUTOR] Результат исполнения для отменённой команды %s проигнорирован (без вторичного TTS)", cmd.id)
                self._executing_command_id = None
                return ProcessResult(
                    decision=RoutingDecision.IGNORED,
                    command_id=cmd.id,
                    turn_id=cmd.turn_id,
                    prompt_to_user=None,
                )

            self._executing_command_id = None
            cmd.state = CommandState.COMPLETED if success else CommandState.FAILED
            logger.info("[STATE] %s -> %s (id=%s)", cmd.intent, cmd.state.value, cmd.id)

            self._active_command = None
            self._current_command_id = None
            self._current_turn_id = 0

        return ProcessResult(
            decision=RoutingDecision.CONSUMED_ACTION,
            command_id=cmd.id,
            turn_id=cmd.turn_id,
            executed_result=result_msg,
            action_name=cmd.intent,
            success=success,
        )
