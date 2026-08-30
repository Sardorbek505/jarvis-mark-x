"""Синтез речи через Microsoft Edge Neural TTS — 100% бесплатный и быстрый резерв.

Используется как мгновенный fallback при исчерпании квоты Fish Audio или сетевых сбоях.
Голоса по умолчанию:
- ru-RU-DmitryNeural (мужской, естественный, близкий к ассистенту)
- ru-RU-SvetlanaNeural (женский)
"""
import io
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_VOICE = os.getenv("EDGE_VOICE", "ru-RU-DmitryNeural")


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


async def speak_pcm(text: str, voice: str = _DEFAULT_VOICE, sample_rate: int = 24000) -> bytes | None:
    """Генерирует raw 16-bit PCM для прямого воспроизведения на десктопе."""
    text = (text or "").strip()
    if not text:
        return None
    mp3_data = await _generate_audio_bytes(text, voice)
    if not mp3_data:
        return None
    try:
        try:
            import imageio_ffmpeg
            from pydub import AudioSegment
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            if ffmpeg_path:
                AudioSegment.converter = ffmpeg_path
        except Exception:
            from pydub import AudioSegment

        seg = AudioSegment.from_file(io.BytesIO(mp3_data), format="mp3")
        seg = seg.set_frame_rate(sample_rate).set_channels(1).set_sample_width(2)
        return seg.raw_data
    except Exception as e:
        logger.warning("Edge-TTS to PCM conversion failed: %s", e)
        return None
