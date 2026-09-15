"""JARVIS Mark X — Классификатор намерений (LLM / Structured Intent Classifier).

Используется в случаях, когда легковесный regex-парсер (FastCommandRouter / SlotFiller)
не может с высокой уверенностью определить интент и параметры из свободной речи.

ВАЖНО:
  Данный компонент СТРОГО классифицирует намерение и извлекает слоты.
  Он НЕ имеет права исполнять инструменты или производить побочные эффекты (side-effects).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from core.command_context import LOCAL_ACTION_INTENTS, CONVERSATIONAL_INTENTS
from core.slot_filler import SlotFiller, normalize_text

logger = logging.getLogger("jarvis-intent-classifier")

CLASSIFICATION_SYSTEM_PROMPT = """Ты — строгий классификатор намерений голосового ассистента Джарвис.
Твоя задача — классифицировать пользовательскую реплику и извлечь слоты параметров в формате JSON.
Никогда не выполняй действия и не отвечай от имени ассистента.

Допустимые интенты:
1. play_media — воспроизведение фильмов, сериалов, аниме, видео.
   Слоты:
     - title (строка, название произведения без слова сериал/фильм)
     - media_type ("series" | "movie" | "video" | null)
     - season (целое число или null)
     - episode (целое число или null)
     - platform ("youtube" | "kinopoisk" | "vkvideo" | null)
2. play_music — воспроизведение музыки или треков через Spotify.
   Слоты:
     - query (строка, поисковый запрос музыки)
3. open_app — запуск локального приложения.
   Слоты:
     - app_name (строка, имя приложения)
4. sleep_timer — таймер сна.
   Слоты:
     - action ("set" | "cancel")
     - duration_minutes (число минут или null)
5. conversation — свободное общение, вопросы о погоде, поиске в сети, фактах и т.д.
   Слоты: {}

Верни ТОЛЬКО валидный JSON следующего вида:
{
  "intent": "play_media" | "play_music" | "open_app" | "sleep_timer" | "conversation",
  "slots": { ... },
  "confidence": 0.0-1.0
}
"""


class LLMIntentClassifier:
    """Классификатор намерений без побочных эффектов."""

    def __init__(self, client: Optional[Any] = None, model_name: str = "gemini-2.5-flash"):
        self.client = client
        self.model_name = model_name

    def classify_intent_sync(self, text: str) -> Dict[str, Any]:
        """
        Синхронная классификация намерения.
        Если клиент LLM не задан или произошла ошибка сети — используется расширенный локальный эвристический разбор.
        """
        clean = normalize_text(text)
        if not clean:
            return {"intent": "conversation", "slots": {}, "confidence": 0.0}

        if self.client is not None:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=f"Классифицируй реплику: {clean}",
                    config={
                        "system_instruction": CLASSIFICATION_SYSTEM_PROMPT,
                        "response_mime_type": "application/json",
                    },
                )
                data = json.loads(response.text)
                if isinstance(data, dict) and "intent" in data:
                    intent = data.get("intent")
                    if intent in LOCAL_ACTION_INTENTS or intent in CONVERSATIONAL_INTENTS:
                        logger.info("[LLM_CLASSIFIER] Распознан интент: %s (conf=%.2f)", intent, data.get("confidence", 1.0))
                        return data
            except Exception as e:
                logger.warning("[LLM_CLASSIFIER] Ошибка вызова LLM: %s. Используется локальный fallback.", e)

        return self._local_fallback_classify(clean)

    async def classify_intent(self, text: str) -> Dict[str, Any]:
        """Асинхронная классификация намерения."""
        return self.classify_intent_sync(text)

    def _local_fallback_classify(self, clean: str) -> Dict[str, Any]:
        """Эвристический локальный разбор для сложных фраз."""
        detected = SlotFiller.detect_new_intent(clean)
        if detected:
            intent, slots = detected
            return {"intent": intent, "slots": slots, "confidence": 0.85}

        media_triggers = ["посмотреть", "глянуть", "вруби", "запили", "покажи", "хочу"]
        if any(w in clean for w in media_triggers) and any(m in clean for m in ["фильм", "сериал", "кино", "сезон", "серию"]):
            slots = SlotFiller.extract_contextual_slots(clean, target_slot="title")
            if "сериал" in clean or "сезон" in clean:
                slots["media_type"] = "series"
            elif "фильм" in clean or "кино" in clean:
                slots["media_type"] = "movie"
            return {"intent": "play_media", "slots": slots, "confidence": 0.75}

        return {"intent": "conversation", "slots": {}, "confidence": 0.5}
