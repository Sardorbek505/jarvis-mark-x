"""
Voice helpers — turn JARVIS's TTS PCM into a Telegram voice note.

Gemini TTS returns raw PCM (24 kHz, 16-bit, mono). Telegram voice notes need
OGG/Opus, so we encode with a portable ffmpeg (imageio-ffmpeg) — no system
package required, works on Render. Falls back gracefully if ffmpeg is missing.
"""
import asyncio
import logging
import struct
import subprocess

logger = logging.getLogger("jarvis-voice")

_SR = 24000  # Gemini TTS sample rate


def _ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        logger.warning(f"ffmpeg unavailable: {e}")
        return None


def pcm_to_wav(pcm: bytes, sr: int = _SR) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container (no encoder needed)."""
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
           struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16) +
           b"data" + struct.pack("<I", len(pcm)))
    return hdr + pcm


def _encode_ogg(pcm: bytes, sr: int) -> bytes | None:
    exe = _ffmpeg_exe()
    if not exe:
        return None
    wav = pcm_to_wav(pcm, sr)
    try:
        p = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error",
             "-f", "wav", "-i", "pipe:0",
             "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"],
            input=wav, capture_output=True, timeout=30,
        )
        if p.returncode == 0 and p.stdout:
            return p.stdout
        logger.warning(f"ffmpeg ogg encode failed: {p.stderr[:160]}")
    except Exception as e:
        logger.warning(f"ffmpeg ogg encode error: {e}")
    return None


async def pcm_to_ogg(pcm: bytes, sr: int = _SR) -> bytes | None:
    """Async wrapper: encode PCM → OGG/Opus off the event loop."""
    if not pcm:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _encode_ogg(pcm, sr))
