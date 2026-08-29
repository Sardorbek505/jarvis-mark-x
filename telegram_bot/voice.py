"""Выбор провайдера синтеза речи.

Один вход для всех, кто озвучивает ответы Джарвиса.
1. Fish Audio — основной (голос JARVIS, готовый Ogg/Opus).
2. Edge-TTS — быстрый бесплатный нейросетевой резерв (Dmitry/Svetlana).
3. Gemini — последний рубеж.
"""
import logging

from telegram_bot import tts_fish
from telegram_bot import tts_edge

logger = logging.getLogger(__name__)


async def speak_ogg(text: str, gemini) -> bytes | None:
    """Ogg/Opus для голосового сообщения. None — если не смог никто."""
    if tts_fish.is_configured():
        audio = await tts_fish.speak_ogg(text)
        if audio:
            return audio
        logger.warning("Fish TTS не справился — пробую Edge-TTS")

    edge_audio = await tts_edge.speak_ogg(text)
    if edge_audio:
        return edge_audio

    logger.warning("Edge-TTS не справился — озвучиваю через Gemini")
    return await gemini.speak_ogg(text)
