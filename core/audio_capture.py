"""JARVIS Mark X — Синхронный аудиозахват (Микрофон + WASAPI Loopback Reference).

Обеспечивает одновременный захват двух потоков:
  1. Входной поток микрофона (16 000 Гц, Mono, int16)
  2. Опорный поток вывода динамиков (WASAPI Loopback, приведенный к 16 000 Гц, Mono, int16)

Потоки синхронизируются по времени и передаются в AFE/AEC процессор.
"""

import logging
import threading
import time
from typing import Callable, Optional
import numpy as np

logger = logging.getLogger("jarvis-audio-capture")

TARGET_SAMPLE_RATE = 16000
BLOCK_SIZE = 1024  # ~64 мс на чанк при 16 кГц


def resample_to_16k(audio_arr: np.ndarray, orig_sr: int) -> np.ndarray:
    """Приводит аудиомассив к 16 000 Гц mono int16."""
    if audio_arr.ndim > 1:
        audio_arr = np.mean(audio_arr, axis=1)

    if orig_sr == TARGET_SAMPLE_RATE:
        return audio_arr.astype(np.int16)

    num_output_samples = int(len(audio_arr) * TARGET_SAMPLE_RATE / orig_sr)
    if num_output_samples <= 0:
        return np.zeros(0, dtype=np.int16)

    orig_indices = np.linspace(0, len(audio_arr) - 1, num_output_samples)
    resampled = np.interp(orig_indices, np.arange(len(audio_arr)), audio_arr)
    return np.clip(resampled, -32768, 32767).astype(np.int16)


class AudioCaptureEngine:
    """
    Движок синхронного захвата аудио:
      - Поток микрофона (16 кГц)
      - Опорный поток динамиков (Render Loopback Reference, 16 кГц)
    """

    def __init__(
        self,
        on_frame: Optional[Callable[[bytes, bytes, float], None]] = None,
        mic_device_index: Optional[int] = None,
    ):
        self.on_frame = on_frame
        self.mic_device_index = mic_device_index
        self._running = False
        self._pyaudio = None
        self._mic_stream = None
        self._loopback_stream = None

        self._ref_lock = threading.Lock()
        self._ref_buffer = bytearray()
        self._max_ref_bytes = TARGET_SAMPLE_RATE * 2 * 4  # 4 секунды кольцевого буфера

    def _get_loopback_device(self, p):
        """Поиск устройства WASAPI Loopback по умолчанию."""
        try:
            wasapi_info = p.get_host_api_info_by_type(p.paWASAPI if hasattr(p, "paWASAPI") else 2)
            default_spk = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            if default_spk.get("isLoopbackDevice", False):
                return default_spk

            if hasattr(p, "get_loopback_device_info_generator"):
                for loopback in p.get_loopback_device_info_generator():
                    if default_spk["name"] in loopback["name"] or "realtek" in loopback["name"].lower() or "speakers" in loopback["name"].lower():
                        return loopback
                for loopback in p.get_loopback_device_info_generator():
                    return loopback
        except Exception as e:
            logger.debug("Loopback device discovery note: %s", e)
        return None

    def start(self):
        """Запуск захвата микрофона и опорного потока."""
        if self._running:
            return
        self._running = True

        try:
            import pyaudiowpatch as pyaudio
            self._pyaudio = pyaudio.PyAudio()
        except ImportError:
            try:
                import pyaudio
                self._pyaudio = pyaudio.PyAudio()
            except Exception as e:
                logger.warning("PyAudio not available: %s", e)
                return

        # 1. Запуск опорного потока (Loopback)
        loopback_dev = self._get_loopback_device(self._pyaudio)
        if loopback_dev:
            try:
                lb_sr = int(loopback_dev["defaultSampleRate"])
                lb_ch = int(loopback_dev["maxInputChannels"])

                def _lb_callback(in_data, frame_count, time_info, status):
                    if in_data and self._running:
                        arr = np.frombuffer(in_data, dtype=np.int16)
                        if lb_ch > 1:
                            arr = arr.reshape(-1, lb_ch)
                        resampled = resample_to_16k(arr, lb_sr)
                        raw_16k = resampled.tobytes()
                        with self._ref_lock:
                            self._ref_buffer.extend(raw_16k)
                            if len(self._ref_buffer) > self._max_ref_bytes:
                                del self._ref_buffer[:-self._max_ref_bytes]
                    return (None, pyaudio.paContinue)

                self._loopback_stream = self._pyaudio.open(
                    format=pyaudio.paInt16,
                    channels=lb_ch,
                    rate=lb_sr,
                    input=True,
                    input_device_index=loopback_dev["index"],
                    frames_per_buffer=BLOCK_SIZE * 2,
                    stream_callback=_lb_callback,
                )
                self._loopback_stream.start_stream()
                logger.info("Acoustic Front-End: Loopback Reference active on [%s]", loopback_dev["name"])
            except Exception as e:
                logger.debug("Loopback stream init note: %s", e)

        # 2. Запуск потока микрофона
        def _mic_callback(in_data, frame_count, time_info, status):
            if not self._running:
                return (None, pyaudio.paComplete)

            ts = time.perf_counter()
            mic_bytes = in_data
            req_bytes = len(mic_bytes)

            with self._ref_lock:
                if len(self._ref_buffer) >= req_bytes:
                    ref_bytes = bytes(self._ref_buffer[:req_bytes])
                    del self._ref_buffer[:req_bytes]
                else:
                    ref_bytes = bytes(self._ref_buffer) + b"\x00" * (req_bytes - len(self._ref_buffer))
                    self._ref_buffer.clear()

            if self.on_frame:
                self.on_frame(mic_bytes, ref_bytes, ts)

            return (None, pyaudio.paContinue)

        try:
            self._mic_stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=TARGET_SAMPLE_RATE,
                input=True,
                input_device_index=self.mic_device_index,
                frames_per_buffer=BLOCK_SIZE,
                stream_callback=_mic_callback,
            )
            self._mic_stream.start_stream()
            logger.info("Acoustic Front-End: Microphone stream active at 16000 Hz")
        except Exception as e:
            logger.error("Failed to open microphone stream: %s", e)

    def stop(self):
        """Остановка аудиозахвата."""
        self._running = False
        if self._mic_stream:
            try:
                self._mic_stream.stop_stream()
                self._mic_stream.close()
            except Exception:
                pass
        if self._loopback_stream:
            try:
                self._loopback_stream.stop_stream()
                self._loopback_stream.close()
            except Exception:
                pass
        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
