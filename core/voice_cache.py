"""Кэш готовых фраз голоса Джарвиса.

Фиксированные реплики («Поставил на паузу, сэр») раньше гонялись через Fish
при каждой команде — 1–2 с днём, до 11 с ночью — ради фразы, которая никогда
не меняется. Здесь такая фраза синтезируется один раз и дальше звучит с диска.

Формат файлов — WAV int16 моно на частоте вывода тракта (24 кГц), то есть
ровно то, что уходит в звуковую карту; ffmpeg не нужен. Ключ включает голос и
частоту: сменили `FISH_VOICE_ID` — старые файлы просто перестают находиться.

В кэш попадает только тембр Fish: Edge-TTS — запасной голос, и закреплять его
на диске значило бы навсегда озвучить фразу «не тем» Джарвисом.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import threading
import wave
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# Длиннее — это уже не «фиксированная реплика», а ответ модели: такие
# повторяются редко, а места занимают много.
MAX_PHRASE_CHARS = 120
MAX_ENTRIES = 300
# Короче ~50 мс звука — это не фраза, а обрывок ответа; такое не закрепляем.
MIN_PCM_BYTES = 2400
# Прогрев: столько отказов подряд — и Fish, видимо, лежит; не долбим дальше.
WARMUP_MAX_FAILURES = 3
# Пауза между фразами прогрева: бесплатный тариф Fish не любит очереди.
WARMUP_PAUSE_SEC = 1.5

_WHITESPACE = re.compile(r"\s+")


def _voice_id() -> str:
    """Идентификатор голоса Fish — часть ключа. Отдельной функцией ради подмены в тестах."""
    try:
        from telegram_bot import tts_fish
        return tts_fish._voice_id()
    except Exception:
        return os.getenv("FISH_VOICE_ID", "")


def normalize_phrase(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip()).lower()


class VoiceCache:
    def __init__(self, directory: Path | str, sample_rate: int = 24000):
        self.directory = Path(directory)
        self.sample_rate = sample_rate
        self._lock = threading.Lock()
        # Счётчик «свежести» вместо mtime: у файловой системы разрешение
        # бывает 1–2 с, и порядок вытеснения становился случайным.
        self._touch_clock = 0
        self._last_used: dict[Path, int] = {}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _logger.warning("Кэш голоса недоступен (%s): %s", self.directory, exc)

    # ── ключи ────────────────────────────────────────────────────────────────
    def _path_for(self, text: str) -> Optional[Path]:
        phrase = normalize_phrase(text)
        if not phrase or len(phrase) > MAX_PHRASE_CHARS:
            return None
        digest = hashlib.sha1(f"{_voice_id()}|{self.sample_rate}|{phrase}".encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.wav"

    # ── чтение / запись ──────────────────────────────────────────────────────
    def get(self, text: str) -> Optional[bytes]:
        path = self._path_for(text)
        if path is None or not path.exists():
            return None
        try:
            with wave.open(str(path), "rb") as wav:
                if wav.getframerate() != self.sample_rate or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                    return None
                pcm = wav.readframes(wav.getnframes())
        except (wave.Error, EOFError, OSError) as exc:
            _logger.debug("Кэш голоса: файл %s не читается (%s)", path.name, exc)
            return None
        if len(pcm) < MIN_PCM_BYTES:
            return None
        with self._lock:
            self._touch_clock += 1
            self._last_used[path] = self._touch_clock
        return pcm

    def put(self, text: str, pcm: bytes) -> bool:
        path = self._path_for(text)
        if path is None or not pcm or len(pcm) < MIN_PCM_BYTES:
            return False
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(pcm[: len(pcm) - (len(pcm) % 2)])
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(buf.getvalue())
            os.replace(tmp, path)  # атомарно: читатель не увидит полуфайл
        except OSError as exc:
            _logger.warning("Кэш голоса: не записал %s: %s", path.name, exc)
            return False
        with self._lock:
            self._touch_clock += 1
            self._last_used[path] = self._touch_clock
        self._evict_if_needed()
        return True

    def has(self, text: str) -> bool:
        path = self._path_for(text)
        return bool(path and path.exists())

    def size(self) -> int:
        try:
            return sum(1 for _ in self.directory.glob("*.wav"))
        except OSError:
            return 0

    # ── вытеснение ───────────────────────────────────────────────────────────
    def _evict_if_needed(self) -> None:
        try:
            files = list(self.directory.glob("*.wav"))
        except OSError:
            return
        if len(files) <= MAX_ENTRIES:
            return
        with self._lock:
            # Файлы, к которым в этой сессии не обращались, считаются самыми
            # давними — по mtime между собой.
            def freshness(p: Path) -> tuple[int, float]:
                return (self._last_used.get(p, 0), p.stat().st_mtime if p.exists() else 0.0)

            for stale in sorted(files, key=freshness)[: len(files) - MAX_ENTRIES]:
                try:
                    stale.unlink()
                    self._last_used.pop(stale, None)
                except OSError:
                    pass


    # ── прогрев ──────────────────────────────────────────────────────────────
    def warmup(self, phrases, synth, pause_sec: float = WARMUP_PAUSE_SEC) -> int:
        """Синтезирует промахи по одной фразе. `synth(text) -> pcm | None`.

        Блокирующий — зовётся из фонового потока. Возвращает число новых
        записей. Останавливается после WARMUP_MAX_FAILURES отказов подряд.
        """
        import time

        done = 0
        failures = 0
        for text in phrases:
            if self.has(text):
                continue
            try:
                pcm = synth(text)
            except Exception as exc:
                _logger.debug("Прогрев голоса: «%s» не синтезировалась: %s", text, exc)
                pcm = None
            if pcm and self.put(text, pcm):
                done += 1
                failures = 0
            else:
                failures += 1
                if failures >= WARMUP_MAX_FAILURES:
                    _logger.warning("Прогрев голоса остановлен: %d отказов подряд", failures)
                    break
            if pause_sec:
                time.sleep(pause_sec)
        if done:
            _logger.info("Прогрев голоса: %d фраз(ы) записано в кэш", done)
        return done


_default: Optional[VoiceCache] = None
_default_lock = threading.Lock()


def get_voice_cache(sample_rate: int = 24000) -> VoiceCache:
    """Общий кэш процесса: `memory/voice_cache/` рядом с data.json."""
    global _default
    with _default_lock:
        if _default is None or _default.sample_rate != sample_rate:
            base = Path(__file__).resolve().parent.parent / "memory" / "voice_cache"
            _default = VoiceCache(base, sample_rate=sample_rate)
        return _default
