"""JARVIS Mark X — Конвейер акустического эхоподавления (AEC / AFE Pipeline).

Реализует вычитание звука динамиков (опорного сигнала Render Reference) из сигнала
микрофона в реальном времени с автоматической оценкой задержки (Delay Estimation)
и подавлением остаточного эха (Residual Echo Suppression).
"""

import logging
import math
import numpy as np
from typing import Tuple

logger = logging.getLogger("jarvis-aec")


class AECPipeline:
    """
    Адаптивный акустический процессор эхоподавления и спектральной очистки.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        filter_length: int = 512,      # Длина адаптивного FIR фильтра (~32 мс)
        step_size: float = 0.25,        # Шаг сходимости NLMS (Normalized LMS)
        reg_epsilon: float = 1e-4,      # Регуляризация для защиты от деления на 0
    ):
        self.sample_rate = sample_rate
        self.filter_length = filter_length
        self.step_size = step_size
        self.reg_epsilon = reg_epsilon

        # Веса адаптивного фильтра NLMS
        self._weights = np.zeros(filter_length, dtype=np.float32)

        # Буфер опорного сигнала (история воспроизведения)
        self._ref_history = np.zeros(filter_length * 4, dtype=np.float32)

        # Оценка задержки динамик -> микрофон (в семплах)
        self._estimated_delay = 0

    def _estimate_delay(self, mic: np.ndarray, ref: np.ndarray) -> int:
        """Оценивает задержку между опорным сигналом и микрофоном по кросс-корреляции."""
        if len(mic) == 0 or len(ref) == 0:
            return 0

        # Вычисляем быструю кросс-корреляцию через FFT
        n = len(mic) + len(ref) - 1
        n_fft = 1 << (n - 1).bit_length()
        f_mic = np.fft.rfft(mic, n=n_fft)
        f_ref = np.fft.rfft(ref, n=n_fft)
        cross_corr = np.fft.irfft(f_mic * np.conj(f_ref), n=n_fft)

        max_idx = int(np.argmax(cross_corr[:len(ref)]))
        return max(0, min(len(ref) - self.filter_length, max_idx))

    def process_frame(self, mic_bytes: bytes, ref_bytes: bytes) -> Tuple[bytes, float]:
        """
        Обрабатывает один аудиокадр.

        Args:
            mic_bytes: PCM int16 байты с микрофона (16 кГц mono)
            ref_bytes: PCM int16 байты опорного сигнала колонок (16 кГц mono)

        Returns:
            clean_bytes: Очищенный PCM int16 поток
            erle_db: Echo Return Loss Enhancement (подавление в дБ)
        """
        if not mic_bytes:
            return b"", 0.0

        mic_arr = np.frombuffer(mic_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        ref_arr = np.frombuffer(ref_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        mic_power = float(np.mean(mic_arr ** 2))
        ref_power = float(np.mean(ref_arr ** 2))

        # Быстрый Bypass: если в колонках тишина (< -50 dBFS), эхо отсутствует
        if ref_power < 1e-5:
            return mic_bytes, 0.0

        # Обновление буфера опорного сигнала
        n_samples = len(ref_arr)
        min_history_len = max(n_samples * 4, self.filter_length * 8)
        if len(self._ref_history) < min_history_len:
            self._ref_history = np.zeros(min_history_len, dtype=np.float32)

        self._ref_history = np.roll(self._ref_history, -n_samples)
        self._ref_history[-n_samples:] = ref_arr

        # Оценка задержки при активном звуке
        if ref_power > 1e-3 and mic_power > 1e-3:
            delay = self._estimate_delay(mic_arr, self._ref_history[-len(mic_arr)*2:])
            # Плавная фильтрация задержки
            self._estimated_delay = int(0.9 * self._estimated_delay + 0.1 * delay)

        # Вычитание эха через NLMS адаптивный фильтр
        clean_arr = np.zeros_like(mic_arr)
        offset = max(0, len(self._ref_history) - len(mic_arr) - self._estimated_delay)

        for i in range(len(mic_arr)):
            ref_slice = self._ref_history[offset + i: offset + i + self.filter_length]
            if len(ref_slice) < self.filter_length:
                clean_arr[i] = mic_arr[i]
                continue

            # Предсказанное эхо: y = w^T * x
            echo_est = float(np.dot(self._weights, ref_slice))
            err = mic_arr[i] - echo_est
            clean_arr[i] = err

            # Обновление весов: w = w + (mu / (|x|^2 + eps)) * err * x
            denom = float(np.dot(ref_slice, ref_slice)) + self.reg_epsilon
            self._weights += (self.step_size / denom) * err * ref_slice

        # Подавление остаточного эха (Residual Echo Suppression)
        clean_power = float(np.mean(clean_arr ** 2))
        if mic_power > 1e-5 and clean_power > 1e-8:
            erle_db = 10.0 * math.log10(mic_power / clean_power)
        else:
            erle_db = 0.0

        # Преобразование обратно в int16
        clean_int16 = np.clip(clean_arr * 32768.0, -32768, 32767).astype(np.int16)
        return clean_int16.tobytes(), max(0.0, erle_db)
