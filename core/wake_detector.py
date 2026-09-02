"""JARVIS Mark X — Двухстадийный детектор ключевого слова (Wake Word Detector).

Архитектура каскада — как в обычном KWS: дешёвая стадия отсекает почти всё,
дорогая подтверждает то немногое, что прошло.

  - Stage 1 (Fast Streaming Candidate Detector):
      Потоковый ONNX-инференс модели 'hey_jarvis' через openWakeWord на 16 кГц.
      Высокая чувствительность (порог 0.38): ловит тихий голос и шёпот, но и
      ошибается чаще. Ниже этого порога выходим сразу, не трогая буферы.

  - Stage 2 (Context Verifier с Pre/Post-roll буфером):
      Кольцевой буфер на 2.0 секунды. Подтверждает кандидата по контексту:
      в окне должна быть энергия живого звука, а пик уверенности за последние
      ~0.5 с — превысить строгий порог 0.50.

Почему пик, а не последний кадр: слово «Джарвис» длиннее одного 80-мс чанка,
и максимум уверенности модели обычно приходится на кадр-два раньше того, на
котором мы проверяем. Брать только последний кадр — терять эти срабатывания.

Прежняя версия применяла строгий порог ТОЛЬКО когда контекста не хватало, то
есть в штатном режиме второй стадии не существовало: всё решали порог 0.38 и
проверка энергии.
"""

import logging
import threading
import time
from typing import Callable, Optional
import numpy as np

logger = logging.getLogger("jarvis-kws")

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # ~80 мс блок для openwakeword

# Сколько последних кадров модели считать «контекстом» слова (~0.5 с).
_CONTEXT_SCORE_FRAMES = 6

# Ниже этого RMS в окне — тишина или шум квантования, а не речь.
_MIN_CONTEXT_RMS = 50.0


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
            self._oww_model.predict(arr)

            jarvis_score, context_score = self._read_scores()

            # Если Stage 1 не сработал — выходим мгновенно
            if jarvis_score < self.threshold_stage1:
                return False

            # Stage 2: Verification with Pre-roll & Context
            # Извлекаем окно из кольцевого буфера: 300мс до + слово + 200мс после
            context_samples = min(len(self._ring_buffer) // 2, int(SAMPLE_RATE * 0.9))
            if context_samples <= 0:
                return False

            context_bytes = bytes(self._ring_buffer[-context_samples * 2:])
            context_arr = np.frombuffer(context_bytes, dtype=np.int16).astype(np.float32)

            rms_energy = float(np.sqrt(np.mean(context_arr ** 2)))
            if rms_energy < _MIN_CONTEXT_RMS:  # Абсолютная тишина / шум квантования
                return False

            # Строгий порог второй стадии. Если истории ещё нет, подтверждать
            # нечем — судим по одному текущему кадру.
            has_context = context_samples >= int(SAMPLE_RATE * 0.4)
            evidence = context_score if has_context else jarvis_score
            if evidence < self.threshold_stage2:
                return False

            # Подтверждение срабатывания
            self._last_wake_time = now
            logger.info(
                "Wake Word: [WAKE CONFIRMED] 'Джарвис' (Score: %.2f, Context: %.2f, Energy RMS: %.0f)",
                jarvis_score, evidence, rms_energy,
            )

            if self.on_wake:
                self.on_wake(jarvis_score)

            return True

    def _read_scores(self) -> tuple:
        """Текущая уверенность модели и пик за последние кадры контекста."""
        for name, history in self._oww_model.prediction_buffer.items():
            if "jarvis" not in name.lower() or len(history) == 0:
                continue
            recent = list(history)[-_CONTEXT_SCORE_FRAMES:]
            return float(history[-1]), float(max(recent))
        return 0.0, 0.0

    def reset(self):
        """Сброс буферов и истории предсказаний."""
        with self._lock:
            self._ring_buffer.clear()
            if self._oww_model and hasattr(self._oww_model, "reset"):
                self._oww_model.reset()
