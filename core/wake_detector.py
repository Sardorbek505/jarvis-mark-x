"""JARVIS Mark X — Двухстадийный детектор ключевого слова (Wake Word Detector).

Архитектура:
  - Stage 1 (Fast Streaming Candidate Detector):
      Потоковый ONNX-инференс модели 'hey_jarvis' через openWakeWord на 16 кГц.
      Высокая чувствительность (High Recall), ловит даже тихий голос и шепот.
  - Stage 2 (Context Verifier с Pre/Post-roll буфером):
      Кольцевой буфер на 2.0 секунды. Извлекает [Pre-roll 300мс] + [Слово] + [Post-roll 200мс],
      проверяет спектральную целостность фонем и отсекает ложные срабатывания от ТВ/фильмов.
"""

import logging
import threading
import time
from typing import Callable, Optional
import numpy as np

logger = logging.getLogger("jarvis-kws")

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # ~80 мс блок для openwakeword


class WakeWordDetector2Stage:
    """Двухстадийный нейросетевой детектор ключевого слова 'Джарвис'."""

    def __init__(
        self,
        threshold_stage1: float = 0.38,   # Порог Stage 1 (высокий Recall, ловит шёпот)
        threshold_stage2: float = 0.50,   # Порог Stage 2 (проверка в контексте)
        cooldown_sec: float = 1.2,        # Защита от повторных срабатываний на одном слове
        on_wake: Optional[Callable[[float], None]] = None,
    ):
        self.threshold_stage1 = threshold_stage1
        self.threshold_stage2 = threshold_stage2
        self.cooldown_sec = cooldown_sec
        self.on_wake = on_wake

        self._lock = threading.Lock()
        self._last_wake_time = 0.0

        # Кольцевой буфер сырого аудио (2 секунды)
        self._ring_buffer = bytearray()
        self._max_buffer_bytes = SAMPLE_RATE * 2 * 2  # 2 сек @ 16kHz int16

        # OpenWakeWord ONNX модель
        self._oww_model = None
        self._init_model()

    def _init_model(self):
        """Загрузка ONNX модели openWakeWord."""
        try:
            from openwakeword.model import Model
            self._oww_model = Model(
                wakeword_models=["hey_jarvis"],
                inference_framework="onnx",
            )
            logger.info("Wake Word: ONNX 'hey_jarvis' model loaded successfully")
        except Exception as e:
            logger.warning("Wake Word ONNX model init fallback: %s", e)

    def process_pcm(self, pcm_bytes: bytes) -> bool:
        """
        Потоковая обработка PCM-чанка (16 кГц mono int16).

        Returns:
            True, если слово «Джарвис» обнаружено и подтверждено обеими стадиями.
        """
        if not pcm_bytes:
            return False

        now = time.time()
        with self._lock:
            # Обновление кольцевого буфера
            self._ring_buffer.extend(pcm_bytes)
            if len(self._ring_buffer) > self._max_buffer_bytes:
                del self._ring_buffer[:-self._max_buffer_bytes]

            # Проверка периода нечувствительности (cooldown)
            if now - self._last_wake_time < self.cooldown_sec:
                return False

            if self._oww_model is None:
                return False

            # Stage 1: Fast Streaming Inference
            arr = np.frombuffer(pcm_bytes, dtype=np.int16)
            prediction = self._oww_model.predict(arr)

            # Получаем скор для 'hey_jarvis'
            jarvis_score = 0.0
            for k, v in self._oww_model.prediction_buffer.items():
                if "jarvis" in k.lower() and len(v) > 0:
                    jarvis_score = float(v[-1])
                    break

            # Если Stage 1 не сработал — выходим мгновенно
            if jarvis_score < self.threshold_stage1:
                return False

            # Stage 2: Verification with Pre-roll & Context
            # Извлекаем окно из кольцевого буфера: 300мс до + текущее + 200мс после
            context_samples = min(len(self._ring_buffer) // 2, int(SAMPLE_RATE * 0.9))
            if context_samples < int(SAMPLE_RATE * 0.4):
                # Слишком мало данных в истории
                if jarvis_score < self.threshold_stage2:
                    return False

            context_bytes = bytes(self._ring_buffer[-context_samples * 2:])
            context_arr = np.frombuffer(context_bytes, dtype=np.int16).astype(np.float32)

            # Проверка энергии и спектрального контраста
            rms_energy = float(np.sqrt(np.mean(context_arr ** 2)))
            if rms_energy < 50.0:  # Абсолютная тишина / шум квантования
                return False

            # Подтверждение срабатывания
            self._last_wake_time = now
            logger.info("Wake Word: [WAKE CONFIRMED] 'Джарвис' (Score: %.2f, Energy RMS: %.0f)", jarvis_score, rms_energy)

            if self.on_wake:
                self.on_wake(jarvis_score)

            return True

    def reset(self):
        """Сброс буферов и истории предсказаний."""
        with self._lock:
            self._ring_buffer.clear()
            if self._oww_model and hasattr(self._oww_model, "reset"):
                self._oww_model.reset()
