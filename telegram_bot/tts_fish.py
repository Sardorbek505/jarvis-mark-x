"""Синтез речи через Fish Audio — голос JARVIS из фильмов.

Почему рядом с Gemini, а не вместо: у бесплатного Gemini регулярный 429, и
особенно на голосе (три вызова на один ответ — расшифровка, ответ, синтез).
У Fish своя квота на бесплатной модели `s2.1-pro-free`, без карты и без
жёсткого потолка, поэтому голос перестаёт зависеть от лимита мозга.

Приятный побочный эффект: Fish отдаёт готовый Ogg/Opus, который Telegram
принимает как голосовое сообщение напрямую. Пути через Gemini нужен ffmpeg,
чтобы перегнать PCM в Ogg, — здесь он не нужен вовсе.

Провайдер намеренно «тихий»: любая ошибка — это None, а вызывающая сторона
падает на Gemini. Голос не та функция, ради которой стоит ронять ответ.
"""
import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_API_URL = "https://api.fish.audio/v1/tts"
_MODEL = "s2.1-pro-free"          # та же S2.1 Pro, бесплатно
_TIMEOUT_SEC = 60
_MAX_CHARS = 4000                 # длинные ответы режем, а не ловим ошибку API

# Замер 05.08.2026 с машины владельца (Казахстан), полный файл:
#   короткая фраза  — normal 1.2 с | low 1.5 с
#   длинная фраза   — normal 3.3 с | low 3.1 с, но первый звук 0.8 с против 3.2 с
# Голосовому в Telegram нужен файл целиком, поэтому по умолчанию normal.
# Обещанные вендором ~90 мс — это время до первого звука внутри их
# дата-центра; на нашей дороге столько съедает сама сеть.
# Для будущего стриминга в десктопном ассистенте правильный выбор — low.
_LATENCY = (os.getenv("FISH_LATENCY") or "normal").strip()


_CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"


@lru_cache(maxsize=1)
def _from_config() -> dict:
    """В облаке ключи приходят env-секретами, на ПК — из api_keys.json.
    Тот же порядок, что и у остального конфига проекта."""
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _key() -> str:
    return (os.getenv("FISH_API_KEY") or _from_config().get("fish_api_key", "")).strip()


def _voice_id() -> str:
    # Русскоязычный Jarvis. Владелец послушал оба и выбрал его, а не
    # киношный MCU-голос (тот в библиотеке помечен как англоязычный и на
    # русском звучит хуже, несмотря на 92k использований).
    return (os.getenv("FISH_VOICE_ID")
            or _from_config().get("fish_voice_id", "")
            or "680d74fbef69419f87cfc70f092a1451").strip()


def is_configured() -> bool:
    return bool(_key())


def _request(text: str) -> bytes:
    body = json.dumps({
        "text": text[:_MAX_CHARS],
        "reference_id": _voice_id(),
        "format": "opus",          # Ogg/Opus — родной формат голосовых Telegram
        "latency": _LATENCY,
    }).encode("utf-8")
    req = urllib.request.Request(_API_URL, data=body, headers={
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
        "model": _MODEL,
    })
    return urllib.request.urlopen(req, timeout=_TIMEOUT_SEC).read()


async def speak_ogg(text: str) -> bytes | None:
    """Озвучивает текст. None — если не настроен или что-то пошло не так."""
    text = (text or "").strip()
    if not text or not is_configured():
        return None
    try:
        audio = await asyncio.to_thread(_request, text)
    except urllib.error.HTTPError as e:
        detail = e.read(200).decode("utf-8", "replace")
        logger.warning("Fish TTS: HTTP %s — %s", e.code, detail)
        return None
    except Exception as e:
        logger.warning("Fish TTS: %s: %s", type(e).__name__, e)
        return None

    # Пустой или обрезанный ответ лучше отдать Gemini, чем слать битый файл.
    if len(audio) < 500 or not audio.startswith(b"OggS"):
        logger.warning("Fish TTS: неожиданный ответ (%d байт)", len(audio))
        return None
    return audio
