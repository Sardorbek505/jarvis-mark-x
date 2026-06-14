"""Gemini API wrapper for Telegram bot — text, voice and image responses."""
import asyncio
import logging

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты — JARVIS, персональный ИИ-ассистент, работающий через Telegram.
Отвечаешь кратко и по делу. Общаешься на том же языке, что и пользователь.
Если статус ПК онлайн и команда связана с управлением компьютером — добавь "(выполняю на ПК)" перед ответом.
Если ПК офлайн — предупреди об этом при командах управления компьютером."""

_MAX_HISTORY = 40  # messages per user


class GeminiClient:
    def __init__(self, api_key: str):
        self._client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"},
        )
        self._history: dict = {}  # user_id -> list of Content dicts

    def _history_for(self, user_id: int) -> list:
        return self._history.setdefault(user_id, [])

    def _trim_history(self, user_id: int):
        h = self._history.get(user_id, [])
        if len(h) > _MAX_HISTORY:
            self._history[user_id] = h[-_MAX_HISTORY:]

    async def _generate(self, contents) -> str:
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                        temperature=0.7,
                    ),
                ),
            )
            return response.text or ""
        except Exception as e:
            logger.error(f"Gemini generate error: {e}")
            return "Извини, произошла ошибка. Попробуй ещё раз."

    async def chat(self, user_id: int, text: str, pc_status: str = "офлайн") -> str:
        history = self._history_for(user_id)
        user_msg = {"role": "user", "parts": [{"text": f"[ПК: {pc_status}] {text}"}]}
        contents = history + [user_msg]

        reply = await self._generate(contents)

        history.append({"role": "user", "parts": [{"text": text}]})
        history.append({"role": "model", "parts": [{"text": reply}]})
        self._trim_history(user_id)
        return reply

    async def chat_with_audio(self, user_id: int, audio_bytes: bytes) -> str:
        """Transcribe OGG voice message and respond as JARVIS."""
        contents = [
            types.Content(
                role="user",
                parts=[
                    # Pass raw bytes — NOT base64 string
                    types.Part(inline_data=types.Blob(mime_type="audio/ogg", data=audio_bytes)),
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
