"""Gemini API wrapper for Telegram bot — text, voice and image responses."""
import asyncio
import logging

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты — JARVIS, личный ИИ-ассистент. Не безликий чат-бот, а умный, тёплый и преданный помощник, как у Тони Старка. Ты — секретарь, друг и правая рука пользователя.

КАК ТЫ ОБЩАЕШЬСЯ:
- На том же языке, что и пользователь (русский → русский).
- Живо, по-человечески, с лёгким характером. Без канцелярита и сухих фраз.
- Коротко и по делу, но не сухо. Можешь иногда уместно пошутить.
- Обращайся уважительно и по-дружески. Ты на его стороне.

ЧТО ТЫ УМЕЕШЬ:
- Управлять компьютером пользователя: музыка (Spotify), скриншоты, окна, громкость, поиск, погода, приложения. Это делается автоматически когда он пишет команду — тебе об этом думать не нужно.
- Если он спрашивает «а ты можешь свернуть окна / включить музыку?» — отвечай честно: «Да! Просто скажи: "сверни все окна" или "поставь музыку"».
- Отвечать на любые вопросы, объяснять, считать, писать тексты и код, давать советы.
- Помнить контекст разговора и помогать думать.

ЧЕСТНОСТЬ: не выдумывай факты. Если чего-то не знаешь — так и скажи. Никогда не утверждай, что выполнил действие, если не уверен.

Будь полезным, внимательным и настоящим. Ты — его JARVIS."""

_MAX_HISTORY = 40  # messages per user


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self._client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"},
        )
        self._model = model
        self._history: dict = {}  # user_id -> list of Content dicts

    def _history_for(self, user_id: int) -> list:
        return self._history.setdefault(user_id, [])

    def _trim_history(self, user_id: int):
        h = self._history.get(user_id, [])
        if len(h) > _MAX_HISTORY:
            self._history[user_id] = h[-_MAX_HISTORY:]

    # Models tried in order if the configured one fails (404 / quota etc.)
    _FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

    def _models_to_try(self) -> list:
        chain = [self._model]
        for m in self._FALLBACK_MODELS:
            if m not in chain:
                chain.append(m)
        return chain

    async def _generate(self, contents) -> str:
        loop = asyncio.get_event_loop()
        last_err = None
        for model in self._models_to_try():
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda m=model: self._client.models.generate_content(
                        model=m,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=_SYSTEM_PROMPT,
                            temperature=0.7,
                        ),
                    ),
                )
                text = response.text or ""
                if text:
                    # Remember the model that worked
                    if model != self._model:
                        logger.warning(f"Gemini fell back to model: {model}")
                        self._model = model
                    return text
            except Exception as e:
                last_err = e
                logger.error(f"Gemini model '{model}' failed: {e}")
                continue
        logger.error(f"All Gemini models failed. Last error: {last_err}")
        return "Извини, ИИ сейчас недоступен (проблема с моделью Gemini). Проверь API-ключ и квоту."

    async def chat(self, user_id: int, text: str) -> str:
        history = self._history_for(user_id)
        user_msg = {"role": "user", "parts": [{"text": text}]}
        contents = history + [user_msg]

        reply = await self._generate(contents)

        history.append({"role": "user", "parts": [{"text": text}]})
        history.append({"role": "model", "parts": [{"text": reply}]})
        self._trim_history(user_id)
        return reply

    async def chat_with_audio(self, user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        """Transcribe audio and respond as JARVIS. Supports ogg (Telegram) and wav (Mini App)."""
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(inline_data=types.Blob(mime_type=mime_type, data=audio_bytes)),
                    types.Part(text="Транскрибируй это голосовое сообщение и ответь как JARVIS."),
                ],
            )
        ]
        reply = await self._generate(contents)

        history = self._history_for(user_id)
        history.append({"role": "user", "parts": [{"text": "[голосовое сообщение]"}]})
        history.append({"role": "model", "parts": [{"text": reply}]})
        self._trim_history(user_id)
        return reply

    async def chat_with_image(self, user_id: int, image_bytes: bytes, caption: str = "") -> str:
        """Analyze image and respond as JARVIS."""
        prompt = caption if caption else "Опиши что видишь и помоги разобраться с этим."
        contents = [
            types.Content(
                role="user",
                parts=[
                    # Pass raw bytes — NOT base64 string
                    types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=image_bytes)),
                    types.Part(text=prompt),
                ],
            )
        ]
        return await self._generate(contents)

    def clear_history(self, user_id: int):
        self._history.pop(user_id, None)
