"""JARVIS Mark X — Главный акустический оркестратор (VoiceTriggerEngine).

Объединяет:
  1. AudioCaptureEngine — синхронный захват микрофона и WASAPI Loopback (динамиков).
  2. AECPipeline — вычитание фоновой музыки/видео из микрофона (Echo Cancellation).
  3. WakeWordDetector2Stage — двухстадийный локальный KWS «Джарвис» (Streaming ONNX).
  4. DuckingController — плавное снижение громкости музыки (-18 dB) при детекции.

Предоставляет единый чистый аудиопоток речи для Gemini Live API.
"""

import logging
import threading
from typing import Callable, Optional

from core.audio_capture import AudioCaptureEngine
from core.aec_pipeline import AECPipeline
from core.wake_detector import WakeWordDetector2Stage
from core.ducking_controller import DuckingState, get_ducking_controller
from core.wakeword import play_activation_chime

logger = logging.getLogger("jarvis-vte")


class VoiceTriggerEngine:
    """Главный оркестратор акустического тракта и голосовой активации."""

    def __init__(
        self,
        on_wake: Optional[Callable[[], None]] = None,
        on_clean_audio: Optional[Callable[[bytes], None]] = None,
        mic_device_index: Optional[int] = None,
        enable_aec: bool = True,
        enable_ducking: bool = True,
    ):
        self.on_wake = on_wake
        self.on_clean_audio = on_clean_audio
        self.enable_aec = enable_aec
        self.enable_ducking = enable_ducking

        self.aec = AECPipeline()
        self.ducking = get_ducking_controller() if enable_ducking else None
        self.wake_detector = WakeWordDetector2Stage(on_wake=self._handle_wake_detected)
        self.capture = AudioCaptureEngine(
            on_frame=self._handle_raw_frame,
            mic_device_index=mic_device_index,
        )

        self.last_erle_db: float = 0.0
        self._running = False
        self._lock = threading.Lock()

    def _handle_wake_detected(self, score: float):
        """Внутренний обработчик подтвержденного ключевого слова."""
        logger.info("VoiceTriggerEngine: 🔔 Wake Triggered (Score: %.2f)", score)

        # 1. Мгновенный Audio Ducking (приглушение музыки)
        if self.ducking:
            self.ducking.duck()

        # 2. Звуковой отклик активации (Chime)
        play_activation_chime()

        # 3. Уведомление главного цикла ассистента
        if self.on_wake:
            try:
                self.on_wake()
            except Exception as e:
                logger.error("on_wake callback error: %s", e)

    def _handle_raw_frame(self, mic_pcm: bytes, ref_pcm: bytes, timestamp: float):
        """Обработка синхронного кадра (микрофон + колонки)."""
        if not self._running:
            return

        # 1. Акустическое эхоподавление (AEC)
        if self.enable_aec:
            clean_pcm, erle = self.aec.process_frame(mic_pcm, ref_pcm)
            self.last_erle_db = erle
        else:
            clean_pcm = mic_pcm
            self.last_erle_db = 0.0

        # 2. Потоковый детектор ключевого слова
        self.wake_detector.process_pcm(clean_pcm)

        # 3. Передача чистого аудиопотока в Gemini Live
        if self.on_clean_audio:
            try:
                self.on_clean_audio(clean_pcm)
            except Exception as e:
                logger.debug("on_clean_audio callback error: %s", e)

    def start(self):
        """Запуск акустического тракта."""
        if self._running:
            return
        self._running = True
        self.capture.start()
        logger.info("VoiceTriggerEngine: Started successfully (AEC=%s, Ducking=%s)", self.enable_aec, self.enable_ducking)

    def stop(self):
        """Остановка акустического тракта."""
        self._running = False
        self.capture.stop()
        if self.ducking:
            self.ducking.restore()
        logger.info("VoiceTriggerEngine: Stopped")

    def set_state(self, state: DuckingState):
        """Переключение состояния жизненного цикла (LISTENING / THINKING / SPEAKING / IDLE)."""
        if self.ducking:
            self.ducking.set_state(state)
