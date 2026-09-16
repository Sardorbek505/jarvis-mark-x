"""Один замок на все короткие звуки (чирп пробуждения, щелчки подтверждения).

`sounddevice.play()` держит ОДИН глобальный поток вывода и при новом вызове
закрывает предыдущий — даже если тот ещё играет из другого потока Python.
При перебивании по слову чирп «проснулся» и щелчок «перебил» стартовали за
миллисекунды из двух потоков, и процесс падал с access violation в ntdll
(живой прогон 17.09.2026, 0xc0000005). Поэтому короткие звуки играют строго
по очереди, под общим замком.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("jarvis-sounds")

_LOCK = threading.Lock()


def play_blocking(audio, sample_rate: int) -> None:
    """Проиграть массив int16/float через sounddevice, дождаться конца.
    Зовётся из фонового потока; конкурентные вызовы выстраиваются в очередь."""
    with _LOCK:
        try:
            import sounddevice as sd
            sd.play(audio, sample_rate)
            sd.wait()
        except Exception as e:
            logger.debug("short sound: %s", e)
