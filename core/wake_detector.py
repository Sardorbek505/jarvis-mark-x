"""JARVIS Mark X — Гибридный локальный детектор ключевого слова (Wake Word Detector).

Архитектура:
  1. Vosk Russian Acoustic Model (KWS на русском языке):
     Потоковая детекция русского одиночного обращения:
     «Джарвис» / «Джервис» / «Жарвис» / «Jarvis».
     Работает полностью локально на CPU без сетевых вызовов.

  2. openWakeWord ONNX Streaming ('hey_jarvis'):
     Потоковый нейросетевой детектор английских и двухсловных обращений:
     «Hey Jarvis» / «Эй Джарвис».
"""

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Optional
import numpy as np

from core import wake_policy

logger = logging.getLogger("jarvis-kws")

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # ~80 мс блок (2560 байт int16)

# Сколько последних кадров openWakeWord считать «контекстом» слова (~0.5 с).
_CONTEXT_SCORE_FRAMES = 6

# Ниже этого RMS в окне — тишина или шум квантования, а не речь.
_MIN_CONTEXT_RMS = 50.0

# Слова-приманки для грамматики Vosk: среди узкого набора он отлично отличает
# «джарвис» от «сервис»/«жалюзи»/«дарвин». Сказал приманку — своя нейросеть
# сработать не должна (если только не уверена на 0.995+: Vosk путает и в
# обратную сторону — «дарвин» на настоящее «Джарвис»).
VOSK_DECOY_WORDS = (
    "сервис", "сервиз", "жалюзи", "дарвин", "джаз", "марвин", "джон", "джем", "джинсы",
    "спасибо", "хорошо", "алиса", "привет", "ярослав", "жарко", "джордж", "джек",
)
DECOY_VETO_SEC = 1.5
DECOY_OVERRIDE_SCORE = 0.995
# Своя модель «Джарвис» (scripts/train_wake_word.py); рядом лежит .json с порогом
CUSTOM_WAKE_MODEL = os.getenv(
    "JARVIS_WAKE_MODEL",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "wake_models", "jarvis_ru.onnx"),
)
# Vosk сказал «джарвис», но своя модель за последнюю секунду ниже этого — не верим
VOSK_NEEDS_NN_SCORE = 0.5
NN_MIN_CONSECUTIVE_FRAMES = 4   # слово держит балл 5–10 кадров, щелчки комнаты — 1–3 (замер 17.09.2026)

WAKE_KEYWORDS = {
    "джарвис",
    "джервис",
    "jarvis",
    "жарвис",
    "дарвис",
    "харвис",
}


class WakeWordDetector2Stage:
    """Гибридный нейросетевой и акустический детектор ключевого слова 'Джарвис'."""

    def __init__(
        self,
        threshold_stage1: float = float(os.getenv("WAKE_THRESHOLD_STAGE1", "0.28")),
        threshold_stage2: float = float(os.getenv("WAKE_THRESHOLD_STAGE2", "0.38")),
        cooldown_sec: float = 1.2,
        on_wake: Optional[Callable[[float], None]] = None,
        on_quick_command: Optional[Callable[[str], None]] = None,
        enable_spotterless: bool = True,
        state_provider: Optional[Callable[[], Any]] = None,
    ):
        self.threshold_stage1 = threshold_stage1
        self.threshold_stage2 = threshold_stage2
        self.cooldown_sec = cooldown_sec
        self.on_wake = on_wake
        self.on_quick_command = on_quick_command
        self.enable_spotterless = enable_spotterless
        self.state_provider = state_provider

        self._lock = threading.Lock()
        self._last_wake_time = 0.0
        self._last_quick_command_time = 0.0
        self._last_decoy_time = 0.0
        self._nn_run = 0
        # Кто засчитал слово последним: "nn" (своя модель) или "vosk"
        self.last_wake_source = ""
        self.quick_command_cooldown = 1.0

        # Кольцевой буфер сырого аудио (2 секунды)
        self._ring_buffer = bytearray()
        self._max_buffer_bytes = SAMPLE_RATE * 2 * 2  # 2 сек @ 16kHz int16

        # Инициализация Vosk (русский KWS)
        self._vosk_model = None
        self._vosk_rec = None
        self._init_vosk()

        # Инициализация openWakeWord (hey_jarvis ONNX)
        self._oww_model = None
        self._init_oww()

    def _init_vosk(self):
        """Загрузка локальной русскоязычной акустической модели Vosk."""
        try:
            import vosk
            # Отключаем лишний шумный вывод библиотеки Vosk/Kaldi в stdout
            vosk.SetLogLevel(-1)
            try:
                self._vosk_model = vosk.Model(lang="ru")
            except Exception:
                cache_dir = os.path.expanduser("~/.cache/vosk/vosk-model-small-ru-0.22")
                if os.path.exists(cache_dir):
                    self._vosk_model = vosk.Model(cache_dir)
            if self._vosk_model:
                # KWS-грамматика для детекции ключевых слов и быстрых команд
                kws_words = [
                    "джарвис", "джервис",
                    "пауза", "стоп", "останови", "остановись",
                    "продолжи", "продолжай", "играй",
                    "следующий", "дальше", "некст", "назад", "предыдущий",
                    "тише", "потише", "громче", "погромче",
                    "звук", "звука", "экран", "полный", "весь",
                    "эй", "слушай", "привет", "окей",
                    *VOSK_DECOY_WORDS,
                    "[unk]",
                ]
                grammar_words = []
                for kw in kws_words:
                    for part in kw.split():
                        if part not in grammar_words:
                            grammar_words.append(part)
                try:
                    import json
                    grammar_json = json.dumps(grammar_words, ensure_ascii=False)
                    self._vosk_rec = vosk.KaldiRecognizer(self._vosk_model, SAMPLE_RATE, grammar_json)
                    logger.info("Wake Word: Vosk Russian KWS grammar recognizer initialized")
                except Exception as ex:
                    logger.warning("Wake Word: Vosk grammar fallback: %s", ex)
                    self._vosk_rec = vosk.KaldiRecognizer(self._vosk_model, SAMPLE_RATE)
        except Exception as e:
            logger.warning("Wake Word: Vosk init fallback: %s", e)

    def _init_oww(self):
        """Загрузка ONNX модели openWakeWord."""
        self._custom_model = False
        try:
            from openwakeword.model import Model
            if os.path.exists(CUSTOM_WAKE_MODEL):
                self._oww_model = Model(wakeword_models=[CUSTOM_WAKE_MODEL], inference_framework="onnx")
                self._custom_model = True
                meta_path = os.path.splitext(CUSTOM_WAKE_MODEL)[0] + ".json"
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        thr = float(json.load(f).get("threshold", 0.94))
                except Exception:
                    thr = 0.94
                # Порог из файла модели — только если порог не задан явно
                # (конструктором или переменной окружения)
                default1 = float(os.getenv("WAKE_THRESHOLD_STAGE1", "0.28"))
                default2 = float(os.getenv("WAKE_THRESHOLD_STAGE2", "0.38"))
                if self.threshold_stage1 == default1 and os.getenv("WAKE_THRESHOLD_STAGE1") is None:
                    self.threshold_stage1 = thr
                if self.threshold_stage2 == default2 and os.getenv("WAKE_THRESHOLD_STAGE2") is None:
                    self.threshold_stage2 = thr
                logger.info("Wake Word: своя модель «Джарвис» загружена (%s, порог %.2f)",
                            os.path.basename(CUSTOM_WAKE_MODEL), thr)
            else:
                self._oww_model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
                logger.info("Wake Word: ONNX 'hey_jarvis' model loaded successfully")
        except Exception as e:
            logger.warning("Wake Word ONNX model init fallback: %s", e)

    @staticmethod
    def _vosk_word_is_wake(word: str) -> bool:
        """Точное слово из списка: «джарвисом» и обрывок «джарви» — не обращение."""
        return word.lower().strip() in WAKE_KEYWORDS

    def _is_spotterless_allowed(self, is_urgent_stop: bool = False) -> bool:
        """Проверяет, разрешены ли spotterless-команды в текущем состоянии жизненного цикла."""
        if not self.enable_spotterless or not self.on_quick_command:
            return False
        if self.state_provider is None:
            return True
        try:
            curr_state = self.state_provider()
            state_name = curr_state.name if hasattr(curr_state, "name") else str(curr_state).upper()
            # В STANDBY споттерлесс полностью заблокирован — реакция только на wake word
            if state_name == "STANDBY":
                return False
            # Во время SPEAKING разрешены только экстренные команды остановки
            if state_name == "SPEAKING":
                return is_urgent_stop
            # В режимах FOLLOW_UP и LISTENING споттерлесс разрешён
            if state_name in ("FOLLOW_UP", "LISTENING"):
                return True
            return False
        except Exception as err:
            logger.debug("state_provider error: %s", err)
            return False

    def process_pcm(self, pcm_bytes: bytes) -> bool:
        """
        Потоковая обработка PCM-чанка (16 кГц mono int16).

        Returns:
            True, если обращение «Джарвис» зафиксировано.
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

            arr = np.frombuffer(pcm_bytes, dtype=np.int16)
            rms_energy = float(np.sqrt(np.mean(np.square(arr.astype(np.float32)))))

            # 1. Потоковая проверка через Vosk (KWS grammar)
            # Vosk обязан получать все фреймы (включая тишину), чтобы закрывать акустические слова
            if self._vosk_rec:
                detected_vosk = False
                matched_word = None

                _is_wake = self._vosk_word_is_wake

                if self._vosk_rec.AcceptWaveform(pcm_bytes):
                    res = json.loads(self._vosk_rec.Result())
                    raw_text = res.get("text", "").lower().strip()
                    if any(d in raw_text.split() for d in VOSK_DECOY_WORDS):
                        self._last_decoy_time = now
                    words = raw_text.split()
                    for w in words:
                        if _is_wake(w):
                            detected_vosk = True
                            matched_word = w
                            break
                    if not detected_vosk and (now - self._last_quick_command_time >= self.quick_command_cooldown):
                        word_list = raw_text.split()
                        if 1 <= len(word_list) <= 3:
                            try:
                                from core.fast_command_router import normalize_command_text
                                clean = normalize_command_text(raw_text)
                                is_urgent = clean in {"пауза", "стоп", "останови", "остановись", "замолчи", "тихо"}
                                if self._is_spotterless_allowed(is_urgent_stop=is_urgent):
                                    if clean in {
                                        "пауза", "стоп", "останови", "остановись",
                                        "продолжи", "продолжай", "возобнови", "играй",
                                        "следующий", "дальше", "некст", "назад", "предыдущий",
                                        "тише", "потише", "сделай тише", "убавь звук",
                                        "громче", "погромче", "сделай громче", "прибавь звук",
                                        "без звука", "полный экран", "на весь экран",
                                    } or any(clean.startswith(p) for p in ("перемотай", "отмотай")):
                                        self._last_quick_command_time = now
                                        self._vosk_rec.Reset()
                                        logger.info("Wake Word: [SPOTTERLESS QUICK COMMAND] '%s'", clean)
                                        try:
                                            self.on_quick_command(clean)
                                        except Exception as e:
                                            logger.error("on_quick_command error: %s", e)
                                        return False
                            except Exception as e:
                                logger.debug("Spotterless parse note: %s", e)
                else:
                    pres = json.loads(self._vosk_rec.PartialResult())
                    partial = pres.get("partial", "").lower().strip()
                    words = partial.split()
                    # Промежуточные результаты грамматики охотно подгоняют любой
                    # похожий звук под «джарвис» — в строгом режиме им не верим,
                    # слово засчитывается только финальным результатом. Экстренное
                    # «стоп»/«пауза» во время речи Джарвиса остаётся: там ошибка
                    # дёшева, а задержка дорога.
                    if not wake_policy.is_strict():
                        for w in words:
                            if _is_wake(w):
                                detected_vosk = True
                                matched_word = w
                                break
                    if not detected_vosk and (
                        self._is_spotterless_allowed(is_urgent_stop=True)
                        and (now - self._last_quick_command_time >= self.quick_command_cooldown)
                        and partial in {"пауза", "стоп"}
                    ):
                        self._last_quick_command_time = now
                        self._vosk_rec.Reset()
                        logger.info("Wake Word: [SPOTTERLESS INSTANT STOP] '%s'", partial)
                        try:
                            self.on_quick_command(partial)
                        except Exception as e:
                            logger.error("on_quick_command error: %s", e)
                        return False

                if detected_vosk and self._custom_model:
                    # Своя модель есть — Vosk лишь подстраховка: его «джарвис»
                    # засчитывается, если сеть за последнюю секунду хоть немного согласна
                    _, nn_peak = self._read_scores()
                    if nn_peak < VOSK_NEEDS_NN_SCORE:
                        logger.info("Wake Word: Vosk услышал '%s', сеть не согласна (%.2f) — пропуск", matched_word, nn_peak)
                        detected_vosk = False
                if detected_vosk:
                    self._last_wake_time = now
                    self._vosk_rec.Reset()
                    logger.info("Wake Word: [VOSK CONFIRMED] '%s'", matched_word)
                    if self.on_wake:
                        try:
                            self.last_wake_source = "vosk"
                            self.on_wake(1.0)
                        except Exception as e:
                            logger.error("on_wake error: %s", e)
                    return True

            # 2. Потоковая проверка через openWakeWord ('hey_jarvis' / 'эй джарвис')
            # Выполняем только при наличии звуковой энергии (экономия CPU в тишине)
            if self._oww_model is not None:
                # Сеть получает ВСЕ кадры, включая тихие: её потоковый спектрограф
                # держит непрерывный буфер, и пропуск кадров ломал признаки
                # (на записях владельца ложных срабатываний было вдвое больше).
                arr = np.frombuffer(pcm_bytes, dtype=np.int16)
                self._oww_model.predict(arr)

                jarvis_score, context_score = self._read_scores()
                # Настоящее слово держит балл над порогом 5–10 кадров подряд,
                # ложное — 1–2; требуем минимум NN_MIN_CONSECUTIVE_FRAMES.
                if jarvis_score >= self.threshold_stage1:
                    self._nn_run += 1
                else:
                    self._nn_run = 0

                if rms_energy >= _MIN_CONTEXT_RMS and jarvis_score >= self.threshold_stage1 and self._nn_run >= NN_MIN_CONSECUTIVE_FRAMES:
                    context_samples = min(len(self._ring_buffer) // 2, int(SAMPLE_RATE * 0.9))
                    if context_samples > 0:
                        has_context = context_samples >= int(SAMPLE_RATE * 0.4)
                        evidence = context_score if has_context else jarvis_score
                        decoy_recent = (now - self._last_decoy_time) < DECOY_VETO_SEC
                        if evidence >= self.threshold_stage2 and decoy_recent and evidence < DECOY_OVERRIDE_SCORE:
                            logger.info("Wake Word: сеть %.2f, но Vosk только что услышал слово-приманку — пропуск", evidence)
                        elif evidence >= self.threshold_stage2:
                            self._last_wake_time = now
                            if self._vosk_rec:
                                self._vosk_rec.Reset()
                            logger.info(
                                "Wake Word: [NN CONFIRMED] %s (Score: %.2f, Context: %.2f)",
                                "джарвис" if self._custom_model else "hey_jarvis",
                                jarvis_score, evidence,
                            )
                            if self.on_wake:
                                try:
                                    self.last_wake_source = "nn"
                                    self.on_wake(jarvis_score)
                                except Exception as e:
                                    logger.error("on_wake error: %s", e)
                            return True

            return False

    def _read_scores(self) -> tuple:
        """Текущая уверенность openWakeWord модели и пик за последние кадры контекста."""
        if not self._oww_model:
            return 0.0, 0.0
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
            self._last_wake_time = 0.0
            self._last_quick_command_time = 0.0
            if self._vosk_rec:
                try:
                    self._vosk_rec.Reset()
                except Exception:
                    pass
            if self._oww_model and hasattr(self._oww_model, "reset"):
                try:
                    self._oww_model.reset()
                except Exception:
                    pass
