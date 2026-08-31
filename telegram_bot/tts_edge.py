"""Синтез речи через Microsoft Edge Neural TTS — 100% бесплатный и быстрый резерв.

Используется как мгновенный fallback при исчерпании квоты Fish Audio или сетевых сбоях.
Голоса по умолчанию:
- ru-RU-DmitryNeural (мужской, естественный, близкий к ассистенту)
- ru-RU-SvetlanaNeural (женский)
"""
import asyncio
import logging
import os
import subprocess
import sys

from telegram_bot.voice_util import _ffmpeg_exe

logger = logging.getLogger(__name__)

_DEFAULT_VOICE = os.getenv("EDGE_VOICE", "ru-RU-DmitryNeural")

# Десктоп собирается windowed (console=False), и без этого флага каждое
# предложение мигало бы чёрным окном консоли.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


async def _generate_audio_bytes(text: str, voice: str = _DEFAULT_VOICE) -> bytes | None:
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks) if chunks else None
    except Exception as e:
        logger.warning("Edge-TTS error: %s: %s", type(e).__name__, e)
        return None


async def speak_ogg(text: str, voice: str = _DEFAULT_VOICE) -> bytes | None:
    """Генерирует аудио для отправки в Telegram."""
    text = (text or "").strip()
    if not text:
        return None
    return await _generate_audio_bytes(text, voice)


def _mp3_to_pcm(mp3: bytes, sample_rate: int) -> bytes | None:
    """MP3 → сырой int16 PCM силами самого ffmpeg, без pydub.

    Через pydub это не работало на машине без СИСТЕМНОГО ffmpeg. Причина не
    в кодировщике: AudioSegment.from_file зовёт ещё и ffprobe (mediainfo_json),
    чтобы определить формат, а портативный imageio-ffmpeg поставляет ТОЛЬКО
    ffmpeg. Замер 31.08.2026 с пустым PATH: converter указывал на встроенный
    бинарь, и всё равно FileNotFoundError [WinError 2] — падал вызов ffprobe,
    а голосовой резерв молча отдавал None.

    Здесь формат входа задан явно (-f mp3), поэтому определять его нечем и
    незачем: хватает одного ffmpeg, который лежит в сборке.
    """
    exe = _ffmpeg_exe()
    if not exe:
        logger.warning("Edge-TTS: ffmpeg недоступен — озвучивать нечем")
        return None
    try:
        p = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error",
             "-f", "mp3", "-i", "pipe:0",
             "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", str(sample_rate), "-ac", "1", "pipe:1"],
            input=mp3, capture_output=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
        if p.returncode == 0 and p.stdout:
            return p.stdout
        logger.warning("Edge-TTS: ffmpeg не декодировал mp3: %s",
                       p.stderr[:160].decode("utf-8", "replace"))
    except Exception as e:
        logger.warning("Edge-TTS: ошибка декодирования: %s: %s", type(e).__name__, e)
    return None


async def speak_pcm(text: str, voice: str = _DEFAULT_VOICE, sample_rate: int = 24000) -> bytes | None:
    """Генерирует raw 16-bit PCM для прямого воспроизведения на десктопе."""
    text = (text or "").strip()
    if not text:
        return None
    mp3_data = await _generate_audio_bytes(text, voice)
    if not mp3_data:
        return None
    # Декодирование блокирующее: в потоке, иначе на секунду встаёт весь
    # голосовой круг, который в это время должен читать сессию.
    return await asyncio.to_thread(_mp3_to_pcm, mp3_data, sample_rate)
