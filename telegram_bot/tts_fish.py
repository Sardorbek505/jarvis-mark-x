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
import threading
import urllib.error
import urllib.request
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


_cfg_cache: tuple[float, dict] = (0.0, {})


def _from_config() -> dict:
    """В облаке ключи приходят env-секретами, на ПК — из api_keys.json: и
    рядом с программой, и в %APPDATA%/JARVIS (туда пишет окно «Ключи» и
    читает JARVIS.exe). Раньше читался только первый — в exe ключа Fish
    «не было», и Джарвис говорил встроенным голосом Gemini. Перечитываем
    раз в 10 с: ключ, вписанный в окне, подхватывается без перезапуска."""
    global _cfg_cache
    import time
    at, data = _cfg_cache
    if time.monotonic() - at > 10 or not at:
        from telegram_bot.local_keys import read
        data = read(_CONFIG_FILE)
        _cfg_cache = (time.monotonic(), data)
    return data


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


def _open(text: str, fmt: str = "opus", latency: str | None = None, sample_rate: int | None = None):
    """Ответ Fish как поток: звук идёт кусками по мере синтеза (как client.tts.stream в их SDK)."""
    payload = {
        "text": text[:_MAX_CHARS],
        "reference_id": _voice_id(),
        "format": fmt,
        "latency": latency or _LATENCY,
    }
    if sample_rate:
        payload["sample_rate"] = sample_rate
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(_API_URL, data=body, headers={
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
        "model": _MODEL,
    })
    return urllib.request.urlopen(req, timeout=_TIMEOUT_SEC)


def _request(text: str, fmt: str = "opus", latency: str | None = None,
             sample_rate: int | None = None) -> bytes:
    with _open(text, fmt, latency, sample_rate) as r:
        return r.read()


def _pcm_from_wav(data: bytes) -> bytes | None:
    """Выковыривает сэмплы из RIFF. Заголовок не фиксированной длины: между
    'fmt ' и 'data' встречаются служебные куски, поэтому ищем 'data', а не
    отрезаем первые 44 байта."""
    if not data.startswith(b"RIFF"):
        return None
    idx = data.find(b"data", 12)
    if idx < 0 or len(data) < idx + 8:
        return None
    return data[idx + 8:]


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


STREAM_BLOCK = 4096


async def stream_pcm(text: str, sample_rate: int = 24000):
    """Голос Джарвиса сырым PCM int16 — ПОТОКОМ: первый кусок звука играет, пока
    остальное ещё синтезируется.

    Раньше десктоп ждал файл целиком (~1 с на предложение, замер 17.08.2026:
    первый кусок balanced 989 мс). S2.1 Pro отдаёт первый звук за ~70 мс у себя в
    дата-центре — выигрыш виден, только если читать ответ по мере прихода.
    Просим сырой pcm; если сервер всё же пришлёт WAV — заголовок снимаем.
    Ошибка до первого звука — исключением (решит, звать ли Edge, вызывающий)."""
    text = (text or "").strip()
    if not text or not is_configured():
        return
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def work():
        try:
            with _open(text, "pcm", "balanced", sample_rate) as r:
                read = getattr(r, "read1", None) or r.read
                head = b""
                while not stop.is_set():
                    data = read(STREAM_BLOCK)
                    if not data:
                        break
                    if head is not None:                     # первые байты: WAV или сырой PCM?
                        head += data
                        if head.startswith(b"RIFF"):
                            idx = head.find(b"data", 12)
                            if idx < 0 or len(head) < idx + 8:
                                continue
                            data = head[idx + 8:]
                        elif len(head) < 4 and b"RIFF".startswith(head):
                            continue
                        else:
                            data = head
                        head = None
                    if data:
                        loop.call_soon_threadsafe(q.put_nowait, data)
        except Exception as exc:                             # HTTP, сеть — отдать наверх
            loop.call_soon_threadsafe(q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    loop.run_in_executor(None, work)
    carry = b""
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            data = carry + item
            n = len(data) // 2 * 2
            carry = data[n:]
            if n:
                yield data[:n]
    finally:
        stop.set()                                           # перебили — поток бросает чтение


async def speak_pcm(text: str, sample_rate: int = 24000) -> bytes | None:
    """Тот же голос, что в Telegram, но сырым PCM — для десктопа (целиком).

    Десктопный ассистент играет int16 напрямую в звуковую карту; Ogg/Opus
    здесь потребовал бы ffmpeg, а Opus вдобавок не умеет 24 кГц (только 48).
    Собирает stream_pcm; для живой речи _fish_worker читает поток сам.
    """
    parts = []
    try:
        async for chunk in stream_pcm(text, sample_rate):
            parts.append(chunk)
    except urllib.error.HTTPError as e:
        detail = e.read(200).decode("utf-8", "replace")
        logger.warning("Fish PCM: HTTP %s — %s", e.code, detail)
        return None
    except Exception as e:
        logger.warning("Fish PCM: %s: %s", type(e).__name__, e)
        return None
    pcm = b"".join(parts)
    if len(pcm) < 500:
        if (text or "").strip() and is_configured():
            logger.warning("Fish PCM: неожиданный ответ (%d байт)", len(pcm))
        return None
    return pcm
