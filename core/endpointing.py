"""JARVIS Mark X — Нейросетевой детектор речи и границы фразы (Silero VAD).

Зачем это вместо порога по громкости.

Энергетический VAD не отличает речь от шума — он отличает громкое от тихого.
Замер на реальном микрофоне (Realtek array, тихая комната): медиана фонового
RMS 86, p90 = 201, p99 = 456. Любой фиксированный порог здесь либо пропускает
шум как речь, либо режет тихую речь. Адаптация под фон помогает, но принципа
не меняет: хлопок двери, щелчок клавиатуры и гул кулера остаются «речью».

Silero VAD — маленькая рекуррентная сеть, обученная именно на разделении речи
и не-речи. Модель уже лежит на диске: её кладёт openwakeword
(`resources/models/silero_vad.onnx`, 1.7 МБ), скачивать ничего не нужно.
Замер на этой машине: 0.38 мс на кадр 64 мс — около 0.6% одного ядра.

Что это даёт сверх «правильнее детектит»:
  * конец фразы определяется по факту окончания речи, а не по спаду громкости;
  * в облако после конца фразы уходит НАСТОЯЩАЯ тишина, поэтому серверный VAD
    Gemini закрывает ход вовремя, а не ждёт, пока стихнет комната;
  * ключевое слово можно проверять только на кадрах с речью — меньше ложных
    срабатываний грамматики Vosk на бытовом шуме.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("jarvis-endpointing")

SAMPLE_RATE = 16000

# Гистерезис: начать считать речью труднее, чем продолжить. Без него на границе
# порога состояние дребезжит каждый кадр и фраза рвётся на куски.
DEFAULT_START_THRESHOLD = 0.50
DEFAULT_END_THRESHOLD = 0.35

# Сколько подряд кадров речи подтверждают начало фразы (отсекает щелчки).
DEFAULT_MIN_SPEECH_FRAMES = 2

# Сколько молчать, чтобы фраза считалась законченной. 300 мс — обычная пауза
# внутри предложения короче, а межфразовая длиннее.
DEFAULT_HANGOVER_MS = 300


def _find_model() -> Optional[str]:
    """Ищет silero_vad.onnx среди уже установленных пакетов."""
    try:
        import importlib.util
        import pathlib

        spec = importlib.util.find_spec("openwakeword")
        if spec and spec.submodule_search_locations:
            root = pathlib.Path(list(spec.submodule_search_locations)[0])
            candidate = root / "resources" / "models" / "silero_vad.onnx"
            if candidate.exists():
                return str(candidate)
    except Exception as e:
        logger.debug("Silero model lookup note: %s", e)
    return None


class SileroEndpointer:
    """Покадровая классификация речи и определение конца фразы.

    Если модель или onnxruntime недоступны, `available` остаётся False —
    вызывающий код обязан откатиться на энергетический порог, а не притворяться,
    что эндпоинтинг работает.
    """

    def __init__(
        self,
        start_threshold: float = DEFAULT_START_THRESHOLD,
        end_threshold: float = DEFAULT_END_THRESHOLD,
        min_speech_frames: int = DEFAULT_MIN_SPEECH_FRAMES,
        hangover_ms: int = DEFAULT_HANGOVER_MS,
        model_path: Optional[str] = None,
    ):
        self.start_threshold = start_threshold
        self.end_threshold = end_threshold
        self.min_speech_frames = min_speech_frames
        self.hangover_ms = hangover_ms

        self._session = None
        self._h = None
        self._c = None
        self.available = False
        self.last_probability = 0.0

        self._in_speech = False
        self._speech_frames = 0
        self._silence_ms = 0.0
        self._turn_had_speech = False

        path = model_path or _find_model()
        if not path:
            logger.warning(
                "Endpointing: silero_vad.onnx не найден — остаётся энергетический порог"
            )
            return
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            # Один поток: модель крошечная, а конвейер и без того многопоточный.
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.log_severity_level = 3
            self._session = ort.InferenceSession(
                path, sess_options=opts, providers=["CPUExecutionProvider"]
            )
            self._reset_state()
            self.available = True
            logger.info("Endpointing: Silero VAD подключён (%s)", path)
        except Exception as e:
            logger.warning("Endpointing: Silero VAD недоступен (%s)", e)

    # ── внутреннее состояние ──────────────────────────────────────────────────
    def _reset_state(self):
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def reset(self):
        """Сброс между ходами диалога: рекуррентное состояние не должно течь."""
        if self._session is not None:
            self._reset_state()
        self._in_speech = False
        self._speech_frames = 0
        self._silence_ms = 0.0
        self._turn_had_speech = False
        self.last_probability = 0.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    # ── основной вход ─────────────────────────────────────────────────────────
    def speech_probability(self, pcm_bytes: bytes) -> float:
        """Вероятность речи в кадре. 0.0, если модель недоступна."""
        if not self.available or not pcm_bytes:
            return 0.0
        try:
            arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            if arr.size == 0:
                return 0.0
            out, h, c = self._session.run(
                None,
                {
                    "input": arr.reshape(1, -1),
                    "sr": np.array(SAMPLE_RATE, dtype=np.int64),
                    "h": self._h,
                    "c": self._c,
                },
            )
            self._h, self._c = h, c
            self.last_probability = float(np.asarray(out).flatten()[0])
            return self.last_probability
        except Exception as e:
            logger.debug("Silero inference note: %s", e)
            return 0.0

    def process(self, pcm_bytes: bytes, frame_ms: float) -> Tuple[bool, bool]:
        """Обрабатывает кадр.

        Возвращает `(is_speech, turn_ended)`:
          * `is_speech` — кадр относится к фразе (включая короткие паузы внутри);
          * `turn_ended` — ровно один раз в момент, когда фраза закончилась.
        """
        if not self.available:
            return (False, False)

        prob = self.speech_probability(pcm_bytes)

        if not self._in_speech:
            if prob >= self.start_threshold:
                self._speech_frames += 1
                if self._speech_frames >= self.min_speech_frames:
                    self._in_speech = True
                    self._turn_had_speech = True
                    self._silence_ms = 0.0
            else:
                self._speech_frames = 0
            return (self._in_speech, False)

        # Внутри фразы
        if prob >= self.end_threshold:
            self._silence_ms = 0.0
            return (True, False)

        self._silence_ms += frame_ms
        if self._silence_ms < self.hangover_ms:
            # Короткая пауза между словами — фраза продолжается.
            return (True, False)

        self._in_speech = False
        self._speech_frames = 0
        turn_ended = self._turn_had_speech
        self._turn_had_speech = False
        return (False, turn_ended)
