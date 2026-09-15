"""JARVIS Mark X — Контекст и состояние команд (Command Context & State).

Отвечает исключительно за жизненный цикл пользовательской задачи (CommandState),
слоты параметров, их слияние (merge) и валидацию готовности к исполнению.
Полностью отделён от низкоуровневого состояния аудио-тракта (ConversationState).
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class CommandState(str, enum.Enum):
    """Жизненный цикл команды пользователя."""
    UNDERSTANDING = "UNDERSTANDING"              # Первоначальный разбор намерения
    COLLECTING_PARAMETERS = "COLLECTING_PARAMETERS"  # Ожидание недостающих слотов от пользователя
    READY_TO_EXECUTE = "READY_TO_EXECUTE"        # Все обязательные параметры собраны
    EXECUTING = "EXECUTING"                      # Задача передана исполнителю
    COMPLETED = "COMPLETED"                      # Успешно выполнена
    FAILED = "FAILED"                            # Завершилась ошибкой
    CANCELLED = "CANCELLED"                      # Отменена пользователем


@dataclass
class SlotDefinition:
    """Определение слота параметра."""
    name: str
    required: bool = False
    prompt_question: str = ""  # Вопрос пользователю, если слот отсутствует
    description: str = ""


@dataclass
class IntentDefinition:
    """Определение интента и его слотов."""
    name: str
    required_slots: List[str] = field(default_factory=list)
    optional_slots: List[str] = field(default_factory=list)
    slot_defs: Dict[str, SlotDefinition] = field(default_factory=dict)
    default_prompt: str = ""
    description: str = ""


@dataclass
class CommandContext:
    """Структурированный контекст активной многошаговой команды."""
    intent: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turn_id: int = 1
    state: CommandState = CommandState.UNDERSTANDING
    slots: Dict[str, Any] = field(default_factory=dict)
    required_slots: List[str] = field(default_factory=list)
    optional_slots: List[str] = field(default_factory=list)
    missing_slots: List[str] = field(default_factory=list)
    target_slot: Optional[str] = None
    last_prompt: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    ttl_seconds: float = 60.0

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Истёк ли срок жизни контекста команды."""
        current = now if now is not None else time.time()
        return (current - self.updated_at) > self.ttl_seconds

    def touch(self, now: Optional[float] = None) -> None:
        """Обновляет время последней активности контекста."""
        self.updated_at = now if now is not None else time.time()

    def next_turn(self) -> int:
        """Увеличивает счётчик шагов диалога и возвращает новый turn_id."""
        self.turn_id += 1
        self.touch()
        return self.turn_id

    def merge_slots(self, new_slots: Dict[str, Any]) -> List[str]:
        """
        Безопасно объединяет новые слоты с существующими.
        Возвращает список обновлённых слотов.
        Не перезаписывает существующие значения значениями None или пустыми строками.
        """
        updated = []
        for k, v in new_slots.items():
            if v is None:
                continue
            if isinstance(v, str) and not v.strip():
                continue
            old_val = self.slots.get(k)
            if old_val != v:
                self.slots[k] = v
                updated.append(k)
        if updated:
            self.touch()
            self.update_missing_slots()
        return updated

    def update_missing_slots(self) -> None:
        """Пересчитывает список отсутствующих обязательных параметров."""
        missing = []
        for slot in self.required_slots:
            val = self.slots.get(slot)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(slot)
        self.missing_slots = missing

        # Если обязательные слоты заполнены:
        if not missing:
            # Для сериала: если season ещё не заполнен и не был задан явно, он может быть целевым уточнением
            if self.intent == "play_media" and self.slots.get("media_type") == "series" and self.slots.get("season") is None and self.target_slot == "season":
                # Мы уже запросили сезон
                pass
            else:
                self.target_slot = None
        else:
            self.target_slot = missing[0]

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь для логов и отладки."""
        return {
            "id": self.id,
            "turn_id": self.turn_id,
            "intent": self.intent,
            "state": self.state.value,
            "slots": dict(self.slots),
            "required_slots": list(self.required_slots),
            "optional_slots": list(self.optional_slots),
            "missing_slots": list(self.missing_slots),
            "target_slot": self.target_slot,
            "last_prompt": self.last_prompt,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ttl_seconds": self.ttl_seconds,
        }


# ─── Разделение Local Actions и Conversational ─────────────────────────────────
LOCAL_ACTION_INTENTS = {"play_media", "play_music", "open_app", "sleep_timer"}
CONVERSATIONAL_INTENTS = {"get_weather", "web_search", "conversation"}


@dataclass
class PendingGeminiTurn:
    """Буфер карантина для ответа Gemini до завершения арбитража владения репликой."""
    utterance_id: str
    generation_id: int
    audio_chunks: List[bytes] = field(default_factory=list)
    output_text_chunks: List[str] = field(default_factory=list)
    total_audio_bytes: int = 0
    max_bytes: int = 1_048_576  # 1 MB RAM limit per turn
    arbitrated: bool = False
    routed_to: Optional[str] = None  # "LOCAL" | "GEMINI" | "DISCARDED"
    # Реплика начата словом «Джарвис» или хоткеем. Помечается в момент
    # срабатывания: само слово съедает локальный детектор, в расшифровку оно
    # не попадает, а окно активности к концу хода успевает истечь.
    addressed: bool = False

    def add_audio(self, data: bytes) -> bool:
        if self.total_audio_bytes + len(data) > self.max_bytes:
            return False
        self.audio_chunks.append(data)
        self.total_audio_bytes += len(data)
        return True

    def add_text(self, text: str) -> None:
        self.output_text_chunks.append(text)

    def full_output_text(self) -> str:
        return "".join(self.output_text_chunks)

    def clear(self) -> None:
        self.audio_chunks.clear()
        self.output_text_chunks.clear()
        self.total_audio_bytes = 0


# ─── Реестр интентов системы ──────────────────────────────────────────────────
INTENT_REGISTRY: Dict[str, IntentDefinition] = {
    "play_media": IntentDefinition(
        name="play_media",
        required_slots=["title"],
        optional_slots=["media_type", "season", "episode", "platform"],
        slot_defs={
            "title": SlotDefinition(name="title", required=True, prompt_question="Какой фильм или сериал включить, сэр?"),
            "season": SlotDefinition(name="season", required=False, prompt_question="Какой сезон, сэр?"),
            "episode": SlotDefinition(name="episode", required=False, prompt_question="Какую серию включить, сэр?"),
            "media_type": SlotDefinition(name="media_type", required=False),
            "platform": SlotDefinition(name="platform", required=False),
        },
        description="Воспроизведение фильмов, сериалов и видеороликов",
    ),
    "play_music": IntentDefinition(
        name="play_music",
        required_slots=["query"],
        optional_slots=["artist", "playlist"],
        slot_defs={
            "query": SlotDefinition(name="query", required=True, prompt_question="Какую песню или исполнителя включить, сэр?"),
        },
        description="Воспроизведение музыки через Spotify",
    ),
    "open_app": IntentDefinition(
        name="open_app",
        required_slots=["app_name"],
        optional_slots=[],
        slot_defs={
            "app_name": SlotDefinition(name="app_name", required=True, prompt_question="Какое приложение открыть, сэр?"),
        },
        description="Запуск приложения на компьютере",
    ),
    "sleep_timer": IntentDefinition(
        name="sleep_timer",
        required_slots=["action"],
        optional_slots=["duration_minutes"],
        slot_defs={
            "action": SlotDefinition(name="action", required=True, prompt_question="Установить или отменить таймер, сэр?"),
            "duration_minutes": SlotDefinition(name="duration_minutes", required=False, prompt_question="На сколько минут поставить таймер, сэр?"),
        },
        description="Управление таймером сна",
    ),
}
