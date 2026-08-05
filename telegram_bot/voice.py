"""Выбор провайдера синтеза речи.

Один вход для всех, кто озвучивает ответы Джарвиса. Fish Audio — основной
(свой бесплатный лимит, голос JARVIS, готовый Ogg/Opus), Gemini — запасной.

Правило то же, что и с памятью: провайдер может умереть, ответ — нет. Если
Fish не настроен или не ответил, пользователь всё равно услышит голос.
"""
import logging

from telegram_bot import tts_fish

logger = logging.getLogger(__name__)


async def speak_ogg(text: str, gemini) -> bytes | None:
    """Ogg/Opus для голосового сообщения. None — если не смог никто."""
    if tts_fish.is_configured():
        audio = await tts_fish.speak_ogg(text)
        if audio:
            return audio
        logger.warning("Fish TTS не справился — озвучиваю через Gemini")
    return await gemini.speak_ogg(text)
