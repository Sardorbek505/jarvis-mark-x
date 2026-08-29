"""JARVIS Mark X — Локальный детектор ключевого слова (Wake-Word) и звуковой отклик.

Позволяет активировать Джарвиса по фразе «Джарвис» или «Jarvis» с воспроизведением
фирменного высокотехнологичного звукового сигнала готовности (Chime).
"""
import enum
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger("jarvis-wakeword")


class WakeWordMode(enum.Enum):
    ALWAYS_ON = "always_on"        # Непрерывный стриминг в Gemini Live
    WAKE_WORD = "wake_word"        # Локальная детекция слова «Джарвис»
    PUSH_TO_TALK = "push_to_talk"  # По горячей клавише


def generate_chime_pcm(sample_rate: int = 24000) -> bytes:
    """Генерирует фирменный футуристичный двухтоновый сигнал активации (Chime)."""
    try:
        import numpy as np

        # Тон 1: 587 Гц (D5), 0.06 сек
        # Тон 2: 880 Гц (A5), 0.12 сек
        t1 = np.linspace(0, 0.06, int(sample_rate * 0.06), False)
        t2 = np.linspace(0, 0.12, int(sample_rate * 0.12), False)

        tone1 = 0.25 * np.sin(2 * np.pi * 587 * t1)
        tone2 = 0.35 * np.sin(2 * np.pi * 880 * t2)

        # Плавное затухание (Envelope)
        decay2 = np.exp(-t2 * 25)
        tone2 = tone2 * decay2

        combined = np.concatenate([tone1, tone2])
        int16_data = (combined * 32767).astype(np.int16)
        return int16_data.tobytes()
    except Exception as e:
        logger.debug("Chime generation failed: %s", e)
        return b""


def play_activation_chime():
    """Воспроизводит звуковой сигнал активации в динамики компьютера."""
    def _worker():
        try:
            import numpy as np
            import sounddevice as sd
            pcm = generate_chime_pcm(24000)
            if pcm:
                arr = np.frombuffer(pcm, dtype=np.int16)
                sd.play(arr, 24000)
                sd.wait()
        except Exception as e:
            logger.debug("Play chime error: %s", e)

    threading.Thread(target=_worker, daemon=True).start()


class WakeWordDetector:
    """Детектор активационного слова с поддержкой переключения режимов."""

    WAKE_KEYWORDS = ("джарвис", "jarvis", "слушай", "компьютер")

    def __init__(
        self,
        mode: WakeWordMode = WakeWordMode.ALWAYS_ON,
        on_wake: Optional[Callable[[], None]] = None,
        device_index: Optional[int] = None,
    ):
        self.mode = mode
        self.on_wake = on_wake
        self.device_index = device_index
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def set_mode(self, mode: WakeWordMode):
        self.mode = mode
        logger.info("WakeWord mode switched to: %s", self.mode.value)

    def trigger_wake(self):
        """Принудительно триггерит пробуждение (например, по горячей клавише Push-to-Talk)."""
        play_activation_chime()
        if self.on_wake:
            self.on_wake()

    def is_keyword_in_text(self, text: str) -> bool:
        """Проверяет наличие ключевого слова в распознанном тексте."""
        text_lower = (text or "").lower().strip()
        return any(kw in text_lower for kw in self.WAKE_KEYWORDS)
