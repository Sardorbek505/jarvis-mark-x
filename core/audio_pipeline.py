"""JARVIS Mark X — Асинхронный аудиоконвейер без блокировок (AudioPipeline).

Архитектура:
  1. Неблокирующий захват аудио:
     PortAudio/WASAPI callback выполняет ИСКЛЮЧИТЕЛЬНО снятие дешёвых признаков
     (текущее состояние, флаг шлюза, опорный кадр из кольцевого буфера) и
     put_nowait в ограниченную очередь. При переполнении отбрасывается самый
     старый кадр с инкрементом drop_count — realtime-поток драйвера не блокируется
     и не делает ни COM-вызовов, ни переходов машины состояний, ни UI-работы.
  2. Фоновый процессинг-воркер (Worker Thread):
     AEC -> уровень для UI -> разрешение состояния -> KWS / Barge-In / VAD -> маршрутизация.
  3. Контроль состояний через ConversationStateMachine:
     Кадр маршрутизируется по состоянию НА МОМЕНТ ЗАХВАТА, а не на момент разбора.
     Иначе речь пользователя, попавшая в очередь до того, как Джарвис начал
     отвечать, выбрасывается как «эхо».
"""

import collections
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional
import numpy as np

from core.conversation_state import ConversationState, ConversationStateMachine
from core.aec_pipeline import AECPipeline
from core.wake_detector import WakeWordDetector2Stage
from core.ducking_controller import get_ducking_controller
from core.endpointing import SileroEndpointer

logger = logging.getLogger("jarvis-audio-pipeline")

DEFAULT_QUEUE_SIZE = 100
MIC_FULL_SCALE = 2400.0
DEFAULT_RMS_THRESHOLD = 80.0
BARGE_IN_RMS_THRESHOLD = 350.0

# Адаптация к шуму комнаты.
# Фиксированный порог здесь не работает: на реальном микрофоне (Realtek array,
# без аппаратного шумоподавления) медиана фонового шума в тихой комнате — RMS 86,
# а 90-й процентиль — 201, при заданном пороге 12. То есть VAD пропускал 87%
# кадров тишины: шум комнаты непрерывно уезжал в облако, а отсчёт молчания
# никогда не начинался, потому что каждый кадр считался речью.
# Порог теперь считается от фактического шума: p90 шумового окна × множитель.
NOISE_WINDOW_FRAMES = 160          # ~5 с при кадре 32 мс
NOISE_MIN_SAMPLES = 40             # раньше этого адаптация не включается
NOISE_SPEECH_MULTIPLIER = 2.5      # во сколько раз речь громче фона
NOISE_BARGE_IN_MULTIPLIER = 4.0    # перебивание требует большего запаса
# Кадр считается фоном (а не речью), только если Silero почти уверен, что речи
# в нём нет. Иначе «Джарвис, …», сказанное в STANDBY, попадало в окно шума и
# порог речи подскакивал до тысяч — микрофон глох (стенд 15.09.2026: p90=2476).
NOISE_MAX_SPEECH_PROBABILITY = 0.2
# Без Silero речь от фона не отличить, поэтому берётся медиана: она устойчива к
# редким громким кадрам, тогда как p90 хватал именно их.
NOISE_PERCENTILE_WITH_VAD = 90
NOISE_PERCENTILE_WITHOUT_VAD = 50

# Окно, в котором ищется речь при проверке ключевого слова.
# Грамматика Vosk состоит из ~25 слов, и любой бытовой шум декодируется в
# ближайшее из них: «дальше», «стоп», «джарвис». Само по себе это неизбежно —
# декодер обязан выдать лучшую гипотезу. Но если нейросетевой VAD за последние
# полсекунды речи не видел вовсе, гипотеза заведомо ложная.
WAKE_SPEECH_LOOKBACK_FRAMES = 8      # ~0.5 с при кадре 64 мс
WAKE_SPEECH_MIN_PROBABILITY = 0.35
# Дольше этого окно вероятностей считается протухшим и вето не накладывается:
# пока Джарвис говорит, окно не обновляется, и wake-word на границе
# SPEAKING → FOLLOW_UP отбрасывался по тишине полусекундной давности.
WAKE_SPEECH_WINDOW_MAX_AGE_SEC = 0.75

# Сколько подряд молчать в LISTENING, прежде чем шлюз закроется сам.
# Без этого окно активности держалось до конца таймера wake-слова (8 с), и всё
# это время комнатный шум выше порога уезжал в Gemini Live. На непрерывном шуме
# модель начинает галлюцинировать — отсюда реплики на случайных языках.
LISTEN_SILENCE_TIMEOUT_SEC = 3.5

# Первые миллисекунды речи Джарвиса AEC ещё не сошёлся — barge-in по RMS там
# сработал бы на собственных динамиках.
AEC_CONVERGENCE_SEC = 0.1

# Состояния, в которых кадр микрофона не нужен вообще.
_DEAF_STATES = (
    ConversationState.MUTED,
    ConversationState.RECONNECTING,
    ConversationState.ERROR,
)


class AudioPipeline:
    """Центральный конвейер обработки аудиопотоков JARVIS Mark X."""

    def __init__(
        self,
        state_machine: Optional[ConversationStateMachine] = None,
        on_wake: Optional[Callable[[], None]] = None,
        on_quick_command: Optional[Callable[[str], None]] = None,
        on_speech_frame: Optional[Callable[[bytes], None]] = None,
        on_barge_in: Optional[Callable[[str], None]] = None,
        on_level: Optional[Callable[[float], None]] = None,
        enable_aec: bool = True,
        enable_ducking: bool = True,
        max_queue_size: int = DEFAULT_QUEUE_SIZE,
        mic_device_index: Optional[int] = None,
        gateway_active_provider: Optional[Callable[[], bool]] = None,
        ref_provider: Optional[Callable[[float, int], bytes]] = None,
        on_silence_timeout: Optional[Callable[[], None]] = None,
        rms_threshold: float = 0.0,
        hangover_frames: int = 13,
        silence_timeout_sec: float = LISTEN_SILENCE_TIMEOUT_SEC,
        noise_multiplier: float = NOISE_SPEECH_MULTIPLIER,
        enable_endpointing: bool = True,
    ):
        self.state_machine = state_machine
        self.on_wake = on_wake
        self.on_quick_command = on_quick_command
        self.on_speech_frame = on_speech_frame
        self.on_barge_in = on_barge_in
        self.on_level = on_level
        self.enable_aec = enable_aec
        self.enable_ducking = enable_ducking
        self.mic_device_index = mic_device_index
        self.gateway_active_provider = gateway_active_provider
        # Источник опорного сигнала колонок (WASAPI loopback) для AEC.
        # Вызывается из аудиоколбэка, поэтому обязан быть дешёвым (срез буфера).
        self.ref_provider = ref_provider
        # Вызывается, когда пользователь замолчал надолго: рантайм закрывает
        # своё окно активности, иначе шлюз откроется обратно на следующем кадре.
        self.on_silence_timeout = on_silence_timeout
        self.rms_threshold = rms_threshold
        self.hangover_frames = hangover_frames
        self.silence_timeout_sec = silence_timeout_sec
        self.noise_multiplier = noise_multiplier
        self._quiet_frames = hangover_frames
        self._last_speech_at: Optional[float] = None
        # Окно наблюдений за фоном. Наполняется ТОЛЬКО в STANDBY: там по
        # определению нет обращённой к нам речи, значит это и есть шум комнаты.
        self._noise_window: collections.deque = collections.deque(maxlen=NOISE_WINDOW_FRAMES)
        self._noise_adapted_logged = False
        # Вероятности речи по последним кадрам — ими проверяется правдоподобность
        # срабатывания ключевого слова.
        self._speech_prob_window: collections.deque = collections.deque(
            maxlen=WAKE_SPEECH_LOOKBACK_FRAMES
        )
        self._speech_prob_updated_at: Optional[float] = None
        self._last_frame_at: Optional[float] = None
        self.wake_vetoed_count = 0
        # Сколько кадров ушло в облако с момента пробуждения. Без этой цифры
        # «Джарвис не отвечает» неотличимо от «Джарвис не слышит».
        self.frames_sent_since_wake = 0

        # Нейросетевой эндпоинтинг. Если модель недоступна, `available` = False
        # и весь тракт честно откатывается на энергетический порог.
        self.endpointer = SileroEndpointer() if enable_endpointing else None
        if self.endpointer is not None and not self.endpointer.available:
            self.endpointer = None

        # Ограниченная очередь входящих аудиокадров
        self.max_queue_size = max_queue_size
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)

        # Счётчики для аудита и телеметрии
        self.drop_count: int = 0
        self.processed_count: int = 0
        self.last_erle_db: float = 0.0
        # Реально ли работает эхоподавление: True только когда пришёл непустой
        # опорный кадр. Без loopback-потока AEC не выполняется, и заявлять
        # обратное в логе нельзя.
        self.aec_active: bool = False

        # Предбуфер (pre-roll) чистых кадров перед началом активной речи (~640 мс)
        self._preroll = collections.deque(maxlen=10)

        # Обработчики тракта
        self.aec = AECPipeline()
        self.ducking = get_ducking_controller() if enable_ducking else None
        self.wake_detector = WakeWordDetector2Stage(
            on_wake=self._handle_wake_spotted,
            on_quick_command=self._handle_quick_command_spotted,
            state_provider=self.current_state,
        )

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

    # ── Состояние ──────────────────────────────────────────────────────────────
    def current_state(self) -> ConversationState:
        """Текущее состояние машины БЕЗ побочных эффектов.

        Используется детектором ключевого слова и аудиоколбэком: оба не должны
        сами двигать машину состояний — переход тянет за собой слушателей
        (UI и COM-дакинг), а им нечего делать в realtime-потоке драйвера.
        """
        if self.state_machine:
            return self.state_machine.state
        return ConversationState.STANDBY

    @staticmethod
    def _frame_ms(pcm_bytes: bytes) -> float:
        """Длительность кадра в миллисекундах (16 кГц, int16 mono)."""
        return len(pcm_bytes) / 2.0 / 16000.0 * 1000.0

    @property
    def noise_floor(self) -> Optional[float]:
        """90-й процентиль фонового шума, если наблюдений уже достаточно."""
        if len(self._noise_window) < NOISE_MIN_SAMPLES:
            return None
        percentile = NOISE_PERCENTILE_WITH_VAD if self.endpointer is not None else NOISE_PERCENTILE_WITHOUT_VAD
        return float(np.percentile(np.fromiter(self._noise_window, dtype=np.float32), percentile))

    def effective_rms_threshold(self) -> float:
        """Порог «это речь», поднятый под фактический шум комнаты."""
        floor = self.noise_floor
        if floor is None:
            return self.rms_threshold
        adapted = floor * self.noise_multiplier
        if adapted > self.rms_threshold and not self._noise_adapted_logged:
            self._noise_adapted_logged = True
            logger.info(
                "AudioPipeline: шум комнаты p90=%.0f — порог речи поднят с %.0f до %.0f",
                floor, self.rms_threshold, adapted,
            )
        return max(self.rms_threshold, adapted)

    def effective_barge_in_threshold(self) -> float:
        """Порог перебивания с тем же запасом по шуму."""
        floor = self.noise_floor
        if floor is None:
            return BARGE_IN_RMS_THRESHOLD
        return max(BARGE_IN_RMS_THRESHOLD, floor * NOISE_BARGE_IN_MULTIPLIER)

    def _gateway_open(self) -> bool:
        if not self.gateway_active_provider:
            return False
        try:
            return bool(self.gateway_active_provider())
        except Exception as e:
            logger.debug("gateway provider note: %s", e)
            return False

    def _resolve_state(self, captured_state: ConversationState, gate_open: bool) -> ConversationState:
        """Согласует машину состояний со шлюзом и возвращает состояние маршрутизации.

        Шлюз (`_wake_active_until` / `_hotkey_active_until` в рантайме) — источник
        правды о том, слушаем ли мы пользователя. Раньше связь была односторонней:
        STANDBY -> LISTENING при открытии шлюза и НИЧЕГО при его закрытии, из-за
        чего машина навсегда залипала в LISTENING: ключевое слово переставало
        требоваться, а весь микрофон непрерывно уезжал в облако.
        """
        # Без шлюза сверять не с чем: машина состояний — единственный источник
        # правды, и трогать её нельзя (иначе явно выставленный LISTENING
        # немедленно сбрасывается в STANDBY).
        if not self.state_machine or not self.gateway_active_provider:
            return captured_state

        state = captured_state
        live_gate_open = self._gateway_open()
        if state == ConversationState.STANDBY and (gate_open or live_gate_open):
            if self.state_machine.state == ConversationState.STANDBY:
                self.state_machine.transition_to(ConversationState.LISTENING, reason="gateway open")
                self._last_speech_at = None
                if self.endpointer is not None:
                    self.endpointer.reset()
            state = ConversationState.LISTENING
        elif state in (ConversationState.LISTENING, ConversationState.FOLLOW_UP) and not gate_open:
            if live_gate_open:
                # Если шлюз прямо сейчас открыт (только что сработал KWS или хоткей),
                # старый кадр из очереди с gate_open=False не должен закрывать сессию!
                state = ConversationState.LISTENING
            else:
                if self.state_machine.state in (ConversationState.LISTENING, ConversationState.FOLLOW_UP):
                    self.state_machine.transition_to(ConversationState.STANDBY, reason="gateway closed")
                state = ConversationState.STANDBY
        return state

    # ── Неблокирующий ввод из callback ─────────────────────────────────────────
    def push_frame(self, mic_pcm: bytes, ref_pcm: bytes = b"", timestamp: float = 0.0) -> bool:
        """
        Кладёт кадр в ограниченную очередь из PortAudio callback.

        ГАРАНТИЯ: никогда не блокирует поток вызова и не делает ничего тяжелее
        чтения enum, сравнения времени и среза кольцевого буфера. При переполнении
        отбрасывает самый старый кадр и инкрементирует drop_count.
        """
        if not self._running or not mic_pcm:
            return False

        if timestamp <= 0.0:
            timestamp = time.monotonic()

        if not ref_pcm and self.ref_provider:
            try:
                ref_pcm = self.ref_provider(timestamp, len(mic_pcm)) or b""
            except Exception:
                ref_pcm = b""

        item = (mic_pcm, ref_pcm, timestamp, self.current_state(), self._gateway_open())

        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            self.drop_count += 1
            # Сбрасываем старый элемент, чтобы освободить место под свежий
            try:
                self._queue.get_nowait()
            except Exception:
                pass
            try:
                self._queue.put_nowait(item)
                return True
            except Exception:
                return False

    # ── Фоновый поток обработки ───────────────────────────────────────────────
    def _worker_loop(self):
        """Главный цикл фонового потока обработки аудио."""
        logger.info("AudioPipeline: Worker thread started")
        while self._running:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                self._process_frame_in_worker(*item)
            except Exception as e:
                # Воркер обязан пережить любой сбой обработки: его смерть означает
                # полную глухоту ассистента до перезапуска процесса.
                logger.error("AudioPipeline: ошибка обработки кадра: %s", e, exc_info=True)
            self.processed_count += 1

        logger.info("AudioPipeline: Worker thread stopped")

    def _process_frame_in_worker(
        self,
        mic_pcm: bytes,
        ref_pcm: bytes,
        timestamp: float,
        captured_state: ConversationState,
        gate_open: bool,
    ):
        """Последовательная обработка аудиокадра в фоновом потоке."""
        self._last_frame_at = timestamp
        # Мьют и обрывы проверяются по ЖИВОМУ состоянию: если микрофон выключили
        # только что, ранее захваченные кадры отправлять уже нельзя.
        if self.current_state() in _DEAF_STATES or captured_state in _DEAF_STATES:
            self._preroll.clear()
            return

        route_state = self._resolve_state(captured_state, gate_open)

        # 1. Акустическое эхоподавление (AEC)
        if self.enable_aec and ref_pcm and any(ref_pcm):
            clean_pcm, erle = self.aec.process_frame(mic_pcm, ref_pcm)
            self.last_erle_db = erle
            if not self.aec_active:
                self.aec_active = True
                logger.info("AudioPipeline: AEC получил опорный сигнал колонок и работает")
        else:
            clean_pcm = mic_pcm
            self.last_erle_db = 0.0

        # 2. Расчёт уровня громкости для UI
        arr = np.frombuffer(clean_pcm, dtype=np.int16)
        rms = float(np.sqrt(np.mean(np.square(arr.astype(np.float32))))) if len(arr) > 0 else 0.0
        if self.on_level:
            try:
                self.on_level(min(1.0, rms / MIC_FULL_SCALE))
            except Exception as e:
                logger.debug("on_level callback note: %s", e)

        # 3. Маршрутизация по состоянию НА МОМЕНТ ЗАХВАТА кадра
        if route_state == ConversationState.SPEAKING:
            self._handle_speaking_frame(clean_pcm, rms, timestamp)
        elif route_state == ConversationState.STANDBY:
            # Режим ожидания — активен только KWS (spotterless заблокирован
            # в _is_spotterless_allowed по состоянию). Заодно это единственное
            # состояние, где заведомо нет обращённой к нам речи, — здесь и
            # измеряется фон комнаты.
            # Vosk обязан получать и тишину, чтобы закрывать акустические слова,
            # поэтому кадр отдаётся всегда. Но вероятность речи считаем здесь же:
            # она пригодится и как признак «в комнате кто-то говорит», и чтобы
            # не записать в фон комнаты само обращение «Джарвис, …».
            speech_prob = self._observe_speech_probability(clean_pcm, timestamp)
            if speech_prob is None or speech_prob < NOISE_MAX_SPEECH_PROBABILITY:
                self._noise_window.append(rms)
            self._preroll.append(clean_pcm)
            self.wake_detector.process_pcm(clean_pcm)
        elif route_state == ConversationState.FOLLOW_UP:
            # Окно непрерывного диалога — разрешён spotterless и прямое начало речи
            self._observe_speech_probability(clean_pcm, timestamp)
            self.wake_detector.process_pcm(clean_pcm)
            if rms >= max(DEFAULT_RMS_THRESHOLD, self.effective_rms_threshold()):
                if self.state_machine:
                    self.state_machine.transition_to(
                        ConversationState.LISTENING, reason="follow-up speech"
                    )
                self._quiet_frames = 0
                self._flush_preroll_and_send(clean_pcm)
            else:
                self._preroll.append(clean_pcm)
        elif route_state == ConversationState.LISTENING:
            # Речь или не речь решает Silero, если он поднялся. Энергетический
            # порог остаётся запасным вариантом: он не отличает речь от шума,
            # а лишь громкое от тихого.
            turn_ended = False
            if self.endpointer is not None:
                is_speech, turn_ended = self.endpointer.process(
                    clean_pcm, self._frame_ms(clean_pcm)
                )
                if is_speech:
                    self._quiet_frames = 0
                    self._last_speech_at = timestamp
                else:
                    self._quiet_frames += 1
                is_loud = is_speech
            else:
                if rms >= self.effective_rms_threshold():
                    self._quiet_frames = 0
                    self._last_speech_at = timestamp
                    is_loud = True
                else:
                    self._quiet_frames += 1
                    is_loud = (self._quiet_frames <= self.hangover_frames)

            if is_loud or self._quiet_frames <= self.hangover_frames:
                self._flush_preroll_and_send(clean_pcm)
            else:
                # Поток в облако НЕ прерывается: VAD Gemini работает по
                # непрерывному звуку, и по дыркам в потоке он не может ни
                # услышать начало речи, ни закрыть ход. Живая проверка это и
                # показала: пока не-речевые кадры просто выбрасывались, модель
                # за весь сеанс выдала одну расшифровку из случайного слова.
                #
                # Поэтому вместо кадра комнаты отправляется ЦИФРОВАЯ ТИШИНА:
                # поток остаётся сплошным, конец хода модель слышит чисто, а
                # шум комнаты в облако не уходит вовсе.
                self._send_frame(self._silence_like(clean_pcm))

            self.frames_sent_since_wake += 1
            if turn_ended:
                self._handle_turn_end()
            elif self._silence_expired(timestamp):
                self._close_gate_on_silence()
        else:
            # THINKING / EXECUTING / INTERRUPTED — ход обрабатывается, копим pre-roll,
            # чтобы не срезать начало следующей реплики.
            self._preroll.append(clean_pcm)

    def _silence_expired(self, timestamp: float) -> bool:
        """Замолчал ли пользователь дольше отведённого окна тишины."""
        if self.silence_timeout_sec <= 0:
            return False
        if self._last_speech_at is None:
            self._last_speech_at = timestamp
            return False
        return (timestamp - self._last_speech_at) >= self.silence_timeout_sec

    def _handle_turn_end(self):
        """Фраза закончилась по нейросетевому эндпоинтеру.

        Шлюз здесь СОЗНАТЕЛЬНО не закрывается, и состояние не меняется.

        Во-первых, скорости это не добавит: ход у Gemini закрывает его
        собственный VAD, когда услышит 400 мс тишины, а мы её уже отправляем —
        хвост hangover длиннее (13 кадров ≈ 830 мс). Обрубив поток здесь, мы
        отдали бы всего ~300 мс тишины и ход завис бы вместо того, чтобы
        закрыться быстрее.

        Во-вторых, ценой был бы обрыв на полуслове: 300 мс — это обычная пауза
        внутри фразы, а не конец реплики.

        Пользы от отметки ровно столько, сколько от неё есть: журнал и сброс
        отсчёта тишины. Всё, что нужно для приватности, уже делает Silero —
        между фразами в облако уходит тишина, а не комната.
        """
        logger.info("AudioPipeline: конец фразы (Silero endpointing)")
        self._last_speech_at = None

    def _close_gate_on_silence(self):
        # Итог захода пишется в журнал: сколько кадров реально ушло в облако.
        """Тишина затянулась — закрываем шлюз и возвращаемся в ожидание."""
        logger.info(
            "AudioPipeline: тишина %.1f с — закрываю шлюз, ухожу в STANDBY "
            "(в облако ушло %d кадров)",
            self.silence_timeout_sec, self.frames_sent_since_wake,
        )
        self._last_speech_at = None
        self._preroll.clear()
        self._quiet_frames = self.hangover_frames
        if self.on_silence_timeout:
            try:
                self.on_silence_timeout()
            except Exception as e:
                logger.error("on_silence_timeout error: %s", e)
        if self.state_machine and self.state_machine.state == ConversationState.LISTENING:
            self.state_machine.transition_to(
                ConversationState.STANDBY, reason="silence timeout"
            )

    def _handle_speaking_frame(self, clean_pcm: bytes, rms: float, timestamp: float):
        """Кадр пришёл, пока Джарвис говорит: ищем перебивание (Barge-In)."""
        speaking_start = getattr(self.state_machine, "_state_entered_at", 0.0) if self.state_machine else 0.0
        self._preroll.clear()
        if timestamp < speaking_start + AEC_CONVERGENCE_SEC:
            return

        should_interrupt = False
        barge_reason = ""

        # AEC уже вычел эхо динамиков, поэтому высокий RMS — это голос в комнате.
        # Без опорного сигнала (aec_active == False) порогу по RMS доверять нельзя:
        # собственные колонки дадут ложное перебивание, поэтому остаётся только KWS.
        if self.aec_active and rms > self.effective_barge_in_threshold():
            should_interrupt = True
            barge_reason = "voice-rms-barge-in"
        elif self.wake_detector.process_pcm(clean_pcm):
            should_interrupt = True
            barge_reason = "wake-word-barge-in"

        if should_interrupt:
            logger.info("AudioPipeline: ⚡ Barge-In detected (%s)", barge_reason)
            if self.on_barge_in:
                try:
                    self.on_barge_in(barge_reason)
                except Exception as e:
                    logger.error("on_barge_in error: %s", e)

    @staticmethod
    def _silence_like(frame: bytes) -> bytes:
        """Кадр цифровой тишины той же длины."""
        return b"\x00" * len(frame)

    def _send_frame(self, frame: bytes):
        """Отправка одного кадра в канал распознавания."""
        if not self.on_speech_frame:
            return
        try:
            self.on_speech_frame(frame)
        except Exception as e:
            logger.debug("Speech frame send note: %s", e)

    def _flush_preroll_and_send(self, current_frame: bytes):
        """Сбрасывает накопленный предбуфер и текущий кадр в канал распознавания."""
        if not self.on_speech_frame:
            self._preroll.clear()
            return
        while self._preroll:
            frame = self._preroll.popleft()
            try:
                self.on_speech_frame(frame)
            except Exception as e:
                logger.debug("Preroll flush note: %s", e)
        try:
            self.on_speech_frame(current_frame)
        except Exception as e:
            logger.debug("Speech frame send note: %s", e)

    def _observe_speech_probability(self, clean_pcm: bytes, timestamp: float) -> Optional[float]:
        """Вероятность речи в кадре по Silero; None, если эндпоинтера нет."""
        if self.endpointer is None:
            return None
        prob = self.endpointer.speech_probability(clean_pcm)
        self._speech_prob_window.append(prob)
        self._speech_prob_updated_at = timestamp
        return prob

    def _speech_seen_recently(self) -> bool:
        """Была ли в последние полсекунды хоть какая-то речь.

        Без эндпоинтера ответ всегда «да»: вето можно накладывать только когда
        есть на чём его обосновать. То же, если окно давно не обновлялось
        (Джарвис говорил сам) — судить по протухшим значениям нельзя.
        """
        if self.endpointer is None or not self._speech_prob_window:
            return True
        if self._speech_prob_updated_at is not None and self._last_frame_at is not None:
            age = self._last_frame_at - self._speech_prob_updated_at
            if age > WAKE_SPEECH_WINDOW_MAX_AGE_SEC:
                return True
        return max(self._speech_prob_window) >= WAKE_SPEECH_MIN_PROBABILITY

    def _handle_wake_spotted(self, score: float):
        """Обработка детекции ключевого слова 'Джарвис'."""
        if not self._speech_seen_recently():
            self.wake_vetoed_count += 1
            logger.info(
                "AudioPipeline: ключевое слово отклонено — речи в комнате не было "
                "(max p=%.2f за %d кадров)",
                max(self._speech_prob_window) if self._speech_prob_window else 0.0,
                len(self._speech_prob_window),
            )
            return
        logger.info("AudioPipeline: 🔔 Wake spotted (score=%.2f)", score)
        if self.ducking:
            try:
                self.ducking.duck()
            except Exception as e:
                logger.debug("Ducking error: %s", e)

        # on_wake обязан отработать ДО transition_to(LISTENING) и слива предбуфера:
        # он открывает шлюз (_wake_active_until) и выставляет окно chime.
        # Если перевести машину состояний в LISTENING до on_wake, шлюз ещё закрыт,
        # и первый же кадр из очереди сбросит машину обратно в STANDBY ("gateway closed").
        if self.on_wake:
            try:
                self.on_wake()
            except Exception as e:
                logger.error("on_wake error: %s", e)

        if self.state_machine:
            self.state_machine.transition_to(ConversationState.LISTENING, reason="wake word")

        self._quiet_frames = 0
        self._last_speech_at = None
        self.frames_sent_since_wake = 0
        self._speech_prob_window.clear()
        if self.endpointer is not None:
            self.endpointer.reset()

        if self.on_speech_frame:
            while self._preroll:
                try:
                    self.on_speech_frame(self._preroll.popleft())
                except Exception:
                    break
        else:
            self._preroll.clear()

    def _handle_quick_command_spotted(self, cmd: str):
        """Обработка мгновенной spotterless-команды."""
        logger.info("AudioPipeline: ⚡ Quick command: '%s'", cmd)
        if self.on_quick_command:
            try:
                self.on_quick_command(cmd)
            except Exception as e:
                logger.error("on_quick_command error: %s", e)

    def start(self):
        """Запуск воркер-потока конвейера."""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="JARVIS-AudioPipelineWorker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("AudioPipeline started successfully")

    def stop(self):
        """Корректная остановка конвейера."""
        self._running = False
        worker = self._worker_thread
        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        self._worker_thread = None
        self._preroll.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                break
        logger.info("AudioPipeline stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Возвращает текущую статистику конвейера."""
        return {
            "queue_size": self._queue.qsize(),
            "max_queue_size": self.max_queue_size,
            "drop_count": self.drop_count,
            "processed_count": self.processed_count,
            "last_erle_db": self.last_erle_db,
            "aec_active": self.aec_active,
            "noise_floor": self.noise_floor,
            "endpointing": "silero" if self.endpointer is not None else "rms",
            "wake_vetoed": self.wake_vetoed_count,
            "frames_sent_since_wake": self.frames_sent_since_wake,
            "rms_threshold": self.effective_rms_threshold(),
            "running": self._running,
        }
