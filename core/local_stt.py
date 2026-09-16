"""Локальное потоковое распознавание команд (Vosk, полный словарь).

Расшифровка от Gemini приходит через 3–5 с ПОСЛЕ конца фразы — всё это время
«сделай громче» просто ждёт. Vosk-small на этой машине разбирает 3-секундную
фразу за ~0.45 с пакетно, а в потоке итог готов через ~0.1 с после конца речи.

Замер 16.09.2026 на фразах стенда: «джарвис сделай громче», «джарвис выключи
музыку», «а ты вчера смотрел матч» — верно; названия городов хуже
(«в шим киньте»), поэтому это движок для КОМАНД: если локальный роутер фразу
не узнал, её, как и раньше, договаривает Gemini со своей расшифровкой.

Модель — та же, что у детектора ключевого слова (один экземпляр в памяти).
"""

from __future__ import annotations

import json
import logging
import threading

logger = logging.getLogger("jarvis-local-stt")

SAMPLE_RATE = 16000


class LocalTranscriber:
    """Потоковый распознаватель одной реплики. Не потокобезопасен сам по себе —
    зовётся из рабочего потока AudioPipeline; `_lock` только на reset/final."""

    def __init__(self, vosk_model=None):
        self._model = vosk_model
        self._rec = None
        self._lock = threading.Lock()
        self._fed_bytes = 0
        # Vosk закрывает сегмент на паузе внутри фразы и отдаёт его текст в
        # Result(); FinalResult() потом содержит только хвост. Копим сегменты.
        self._segments: list[str] = []
        self.available = False
        if vosk_model is None:
            vosk_model = self._load_model()
            self._model = vosk_model
        if self._model is not None:
            try:
                import vosk
                self._rec = vosk.KaldiRecognizer(self._model, SAMPLE_RATE)
                self._rec.SetWords(False)
                self.available = True
                logger.info("LocalSTT: Vosk (полный словарь) готов")
            except Exception as e:
                logger.warning("LocalSTT: Vosk recognizer не создан: %s", e)

    @staticmethod
    def _load_model():
        try:
            import os
            import vosk
            vosk.SetLogLevel(-1)
            try:
                return vosk.Model(lang="ru")
            except Exception:
                cache_dir = os.path.expanduser("~/.cache/vosk/vosk-model-small-ru-0.22")
                if os.path.exists(cache_dir):
                    return vosk.Model(cache_dir)
        except Exception as e:
            logger.debug("LocalSTT: модель не загружена: %s", e)
        return None

    def reset(self) -> None:
        if self._rec is None:
            return
        with self._lock:
            try:
                self._rec.Reset()
            except Exception as e:
                logger.debug("LocalSTT reset: %s", e)
            self._fed_bytes = 0
            self._segments.clear()

    def accept(self, pcm_bytes: bytes) -> None:
        """Кадр речи (16 кГц int16). Итоги промежуточных сегментов копятся внутри Vosk."""
        if self._rec is None or not pcm_bytes:
            return
        try:
            if self._rec.AcceptWaveform(pcm_bytes):
                seg = json.loads(self._rec.Result()).get("text", "").strip()
                if seg:
                    self._segments.append(seg)
            self._fed_bytes += len(pcm_bytes)
        except Exception as e:
            logger.debug("LocalSTT accept: %s", e)

    def fed_seconds(self) -> float:
        return self._fed_bytes / 2.0 / SAMPLE_RATE

    def final(self) -> str:
        """Текст реплики целиком и сброс под следующую."""
        if self._rec is None:
            return ""
        with self._lock:
            try:
                tail = json.loads(self._rec.FinalResult()).get("text", "").strip()
            except Exception as e:
                logger.debug("LocalSTT final: %s", e)
                tail = ""
            parts = [*self._segments, tail] if tail else list(self._segments)
            self._segments.clear()
            self._fed_bytes = 0
            return " ".join(p for p in parts if p).strip()

    def partial(self) -> str:
        if self._rec is None:
            return ""
        try:
            return json.loads(self._rec.PartialResult()).get("partial", "").strip()
        except Exception:
            return ""
