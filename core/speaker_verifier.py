"""Голосовой отпечаток владельца: отвечать только ему.

Resemblyzer (GE2E, 256-мерный эмбеддинг) на CPU: ~130 мс на 2 с речи.
Эталон — банк эмбеддингов записей владельца (`scripts/enroll_owner_voice.py`
→ `config/owner_voice.npy`, матрица N×256); сходство = среднее по трём
ближайшим записям. Калибровка 16.09.2026 (63 записи владельца против
синтетических голосов Silero/SAPI, leave-one-out): свой min 0.62, чужой max
0.59 → порог 0.62. Среднее-эталон разделяло хуже (0.61 против 0.62).

Включается `JARVIS_OWNER_ONLY=1` (по умолчанию выключено: иначе домашние не
смогут пользоваться). Порог — `JARVIS_OWNER_THRESHOLD`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("jarvis-speaker")

SAMPLE_RATE = 16000
DEFAULT_THRESHOLD = 0.62
TOP_K = 3
MIN_AUDIO_SEC = 0.8
MAX_AUDIO_SEC = 6.0
ENROLLMENT_PATH = Path(__file__).resolve().parent.parent / "config" / "owner_voice.npy"


def is_enabled() -> bool:
    return os.getenv("JARVIS_OWNER_ONLY", "0").strip() in ("1", "true", "yes", "on")


class SpeakerVerifier:
    def __init__(self, enrollment: Optional[np.ndarray] = None, threshold: Optional[float] = None):
        self.threshold = float(threshold if threshold is not None else os.getenv("JARVIS_OWNER_THRESHOLD", DEFAULT_THRESHOLD))
        self._encoder = None
        self.enrollment: Optional[np.ndarray] = None
        if enrollment is None and ENROLLMENT_PATH.exists():
            try:
                enrollment = np.load(ENROLLMENT_PATH)
            except Exception as exc:
                logger.warning("Эталон голоса не прочитан: %s", exc)
        if enrollment is not None:
            bank = np.atleast_2d(np.asarray(enrollment, dtype=np.float32))
            self.enrollment = bank / (np.linalg.norm(bank, axis=1, keepdims=True) + 1e-9)

    @property
    def available(self) -> bool:
        return self.enrollment is not None

    def _get_encoder(self):
        if self._encoder is None:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder("cpu", verbose=False)
        return self._encoder

    def embed(self, pcm16: np.ndarray) -> Optional[np.ndarray]:
        """Эмбеддинг речи (16 кГц int16). None, если речи слишком мало."""
        if pcm16 is None or len(pcm16) < int(MIN_AUDIO_SEC * SAMPLE_RATE):
            return None
        pcm16 = pcm16[-int(MAX_AUDIO_SEC * SAMPLE_RATE):]
        try:
            from resemblyzer import preprocess_wav
            wav = preprocess_wav(pcm16.astype(np.float32) / 32768.0, source_sr=SAMPLE_RATE)
            if len(wav) < int(MIN_AUDIO_SEC * SAMPLE_RATE) * 0.5:
                return None
            return self._get_encoder().embed_utterance(wav)
        except Exception as exc:
            logger.debug("Эмбеддинг голоса не посчитан: %s", exc)
            return None

    def score(self, pcm16: np.ndarray) -> Optional[float]:
        if self.enrollment is None:
            return None
        e = self.embed(pcm16)
        if e is None:
            return None
        sims = self.enrollment @ (e / (np.linalg.norm(e) + 1e-9))
        k = min(TOP_K, len(sims))
        return float(np.sort(sims)[-k:].mean())

    def is_owner(self, pcm16: np.ndarray) -> tuple[bool, Optional[float]]:
        """(свой ли голос, сходство). Нет эталона или мало речи — считаем своим:
        проверка не должна делать ассистента глухим из-за своих же проблем."""
        s = self.score(pcm16)
        if s is None:
            return True, None
        return s >= self.threshold, s


_default: Optional[SpeakerVerifier] = None


def get_speaker_verifier() -> Optional[SpeakerVerifier]:
    """Общий верификатор, если режим включён и эталон есть; иначе None."""
    global _default
    if not is_enabled():
        return None
    if _default is None:
        v = SpeakerVerifier()
        if not v.available:
            logger.warning("JARVIS_OWNER_ONLY=1, но эталона голоса нет — запустите scripts/enroll_owner_voice.py")
            return None
        _default = v
    return _default
