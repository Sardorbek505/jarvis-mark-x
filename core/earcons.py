"""JARVIS Mark X — Процедурный синтез и воспроизведение звуковых сигналов (Earcons).

Высокотехнологичный звуковой дизайн акустической обратной связи:
  - WAKE: двухтоновый восходящий колокольчик активации (D5 -> A5, 120 мс)
  - SUCCESS: ультракороткий мягкий высокочастотный щелчок подтверждения (C6 + E6, 40 мс)
  - ERROR: мягкий нисходящий сигнал отказа / ошибки (E4 -> D4, 60 мс)
  - MUTE / UNMUTE: щелчки переключения состояния микрофона (25 мс)

Все звуки синтезируются процедурно через numpy (без внешних аудиофайлов),
кэшируются в памяти и воспроизводятся через sounddevice в фоновом daemon-потоке.
"""

import logging
import threading
from typing import Dict, Optional
import numpy as np

logger = logging.getLogger("jarvis-earcons")

SAMPLE_RATE = 24000

# Предварительно скомпилированные PCM-буферы в формате float32 / int16
_PCM_CACHE: Dict[str, np.ndarray] = {}


def _synthesize_earcon(kind: str, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Процедурный математический синтез звуков с плавными огибающими."""
    if kind == "wake":
        # Двухтоновый сигнал: D5 (587 Гц) 60мс + A5 (880 Гц) 120мс
        t1 = np.linspace(0, 0.06, int(sr * 0.06), False)
        t2 = np.linspace(0, 0.12, int(sr * 0.12), False)
        tone1 = 0.22 * np.sin(2 * np.pi * 587 * t1)
        tone2 = 0.32 * np.sin(2 * np.pi * 880 * t2) * np.exp(-t2 * 25)
        return np.concatenate([tone1, tone2]).astype(np.float32)

    elif kind == "success":
        # Ультракороткий стеклянный щелчок подтверждения действия: C6 (1046 Гц) + E6 (1318 Гц), 40 мс
        dur = 0.045
        t = np.linspace(0, dur, int(sr * dur), False)
        harm1 = 0.22 * np.sin(2 * np.pi * 1046 * t)
        harm2 = 0.18 * np.sin(2 * np.pi * 1318 * t)
        harm3 = 0.08 * np.sin(2 * np.pi * 2093 * t)
        decay = np.exp(-t * 90)
        return ((harm1 + harm2 + harm3) * decay).astype(np.float32)

    elif kind == "error":
        # Нисходящий тон отказа: E4 (330 Гц) -> D4 (293 Гц), 70 мс
        t1 = np.linspace(0, 0.035, int(sr * 0.035), False)
        t2 = np.linspace(0, 0.045, int(sr * 0.045), False)
        tone1 = 0.25 * np.sin(2 * np.pi * 330 * t1) * np.exp(-t1 * 20)
        tone2 = 0.25 * np.sin(2 * np.pi * 293 * t2) * np.exp(-t2 * 30)
        return np.concatenate([tone1, tone2]).astype(np.float32)

    elif kind == "mute":
        # Короткий нисходящий щелчок закрытия микрофона
        dur = 0.03
        t = np.linspace(0, dur, int(sr * dur), False)
        tone = 0.20 * np.sin(2 * np.pi * (500 - 250 * (t / dur)) * t) * np.exp(-t * 80)
        return tone.astype(np.float32)

    elif kind == "unmute":
        # Короткий восходящий щелчок открытия микрофона
        dur = 0.03
        t = np.linspace(0, dur, int(sr * dur), False)
        tone = 0.20 * np.sin(2 * np.pi * (250 + 250 * (t / dur)) * t) * np.exp(-t * 80)
        return tone.astype(np.float32)

    return np.zeros(int(sr * 0.01), dtype=np.float32)


def _warmup_cache():
    """Предварительная генерация всех сигналов при загрузке модуля."""
    for name in ("wake", "success", "error", "mute", "unmute"):
        try:
            _PCM_CACHE[name] = _synthesize_earcon(name)
        except Exception as e:
            logger.debug("Warmup error for %s: %s", name, e)


# Предзагрузка буферов
_warmup_cache()


def play_earcon(kind: str, async_play: bool = True):
    """
    Воспроизводит звуковой сигнал заданного типа.
    kind: 'wake' | 'success' | 'error' | 'mute' | 'unmute'
    """
    audio = _PCM_CACHE.get(kind)
    if audio is None or len(audio) == 0:
        return

    def _play_task():
        try:
            import sounddevice as sd
            sd.play(audio, SAMPLE_RATE)
            sd.wait()
        except Exception as e:
            logger.debug("play_earcon exception: %s", e)

    if async_play:
        threading.Thread(target=_play_task, daemon=True).start()
    else:
        _play_task()


def play_activation_chime():
    """Сигнал активации (Chime)."""
    play_earcon("wake")


def play_success_earcon():
    """Фирменный щелчок подтверждения выполненного действия."""
    play_earcon("success")


def play_error_earcon():
    """Звуковой сигнал ошибки."""
    play_earcon("error")


def play_mute_earcon(is_muted: bool = True):
    """Звуковой щелчок мьюта / размьюта."""
    play_earcon("mute" if is_muted else "unmute")
