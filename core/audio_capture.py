"""JARVIS Mark X — Синхронный аудиозахват (Микрофон + WASAPI Loopback Reference).

Обеспечивает одновременный захват двух потоков:
  1. Входной поток микрофона (16 000 Гц, Mono, int16)
  2. Опорный поток вывода динамиков (WASAPI Loopback, приведенный к 16 000 Гц, Mono, int16)

Потоки идут на независимых часах и разными блоками, поэтому опорный сигнал
держится в кольцевом буфере с меткой времени последнего семпла, а под каждый
кадр микрофона из него берётся окно за тот же промежуток времени. Остаточный
сдвиг (длина блока плюс задержка драйвера) добирает оценка задержки в AEC.
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
        # Момент, которому соответствует ПОСЛЕДНИЙ семпл в буфере. Без него
        # выровнять потоки нельзя: они идут на разных часах и разными блоками.
        self._ref_end_time: Optional[float] = None

    # ── Опорный поток: запись и выборка по времени ────────────────────────────
    def _push_ref(self, pcm_bytes: bytes, ts: float):
        """Кладёт блок опорного сигнала, помечая его временем прихода."""
        if not pcm_bytes:
            return
        with self._ref_lock:
            self._ref_buffer.extend(pcm_bytes)
            if len(self._ref_buffer) > self._max_ref_bytes:
                del self._ref_buffer[:-self._max_ref_bytes]
            self._ref_end_time = ts

    def _ref_window(self, ts: float, n_bytes: int) -> bytes:
        """Опорный сигнал за то же окно времени, что и кадр микрофона.

        Раньше здесь была очередь: микрофонный колбэк забирал n байт С НАЧАЛА
        буфера и удалял их. Потоки идут на разных часах и разными блоками,
        поэтому очередь неизбежно разъезжается. Если loopback наполняет буфер
        быстрее, чем микрофон вычерпывает, обрезка выбрасывает самое старое —
        а читали как раз самое старое, и опорный сигнал отставал на секунды.
        Если медленнее — выдавалась тишина, и сдвиг закреплялся навсегда.
        Компенсировать такое нечем: AEC ищет задержку в пределах 250 мс.

        Теперь буфер — кольцо, из которого берут окно по метке времени, а
        ничего не удаляют. Постоянная составляющая ошибки (длина блока плюс
        задержка драйвера) остаётся, но она мала и стабильна — её дожимает
        оценка задержки в AEC.
        """
        silence = b"\x00" * n_bytes
        with self._ref_lock:
            if self._ref_end_time is None or not self._ref_buffer:
                return silence

            buffer_len = len(self._ref_buffer)
            # На сколько семплов кадр микрофона «моложе» последнего опорного.
            lag_samples = int((self._ref_end_time - ts) * TARGET_SAMPLE_RATE)
            end = buffer_len - lag_samples * 2
            start = end - n_bytes

            if end <= 0 or start >= buffer_len:
                return silence          # окна ещё/уже нет в буфере

            head_pad = b""
            if start < 0:
                head_pad = b"\x00" * (-start)
                start = 0

            tail_pad = b""
            if end > buffer_len:
                tail_pad = b"\x00" * (end - buffer_len)
                end = buffer_len

            segment = head_pad + bytes(self._ref_buffer[start:end]) + tail_pad

        if len(segment) < n_bytes:
            segment += b"\x00" * (n_bytes - len(segment))
        return segment[:n_bytes]

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
                        self._push_ref(resampled.tobytes(), time.perf_counter())
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
            ref_bytes = self._ref_window(ts, len(mic_bytes))

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
