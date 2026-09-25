"""Сколько звука прямо сейчас идёт из динамиков этого компьютера.

Зачем: микрофон слышит комнату целиком, включая музыку из собственных
колонок. В логе это выглядело так — Джарвис прилежно расшифровывал
узбекскую песню и отвечал ей, а живую речь рядом не разбирал.

Отделить одно от другого по громкости нельзя: замерено, музыка даёт на
микрофоне RMS 7000-14000, ровно как речь. Аппаратное шумоподавление ASUS
тоже не помогает — оно снимает вентилятор и клавиатуру, но не музыку
(сравнил оба устройства ввода: медианы 5548 и 5656, разницы нет).

Зато Windows точно знает, что сейчас звучит в колонках, — это и берём.
Пока компьютер играет, микрофон не слушаем.

Замер идёт в отдельном потоке: COM нельзя дёргать из аудио-колбэка, а сам
колбэк обязан возвращаться за миллисекунды.
"""
import logging
import threading

logger = logging.getLogger("jarvis-speaker")

_POLL_SEC = 0.05          # 50 мс — быстрее, чем длится один кадр микрофона
_DEVICE_CHECK_SEC = 2.0   # как часто сверяться с устройством вывода по умолчанию


class SpeakerMeter:
    """Пиковый уровень вывода в [0..1]. 0.0, если измерить нечем."""

    def __init__(self, poll_sec: float = _POLL_SEC):
        self._peak = 0.0
        self._poll = poll_sec
        self._stop = threading.Event()
        self._thread = None
        self.available = False

    # ── жизненный цикл ───────────────────────────────────────────────────────
    def start(self) -> bool:
        try:
            self._make_meter()
        except Exception as exc:
            # Не смертельно: без измерителя просто нет этой защиты.
            logger.warning("Уровень динамиков недоступен (%s) — фильтр выключен", exc)
            return False
        self.available = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="speaker-meter")
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    @property
    def peak(self) -> float:
        return self._peak

    # ── внутреннее ───────────────────────────────────────────────────────────
    @staticmethod
    def _default_id() -> str:
        """Идентификатор текущего устройства вывода по умолчанию."""
        from pycaw.pycaw import AudioUtilities
        device = AudioUtilities.GetSpeakers()
        raw = getattr(device, "_dev", None) or device
        try:
            return str(raw.GetId())
        except Exception:
            return ""

    @staticmethod
    def _make_meter():
        from comtypes import CLSCTX_ALL, cast, POINTER
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        device = AudioUtilities.GetSpeakers()
        raw = getattr(device, "_dev", None) or device
        return cast(raw.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None),
                    POINTER(IAudioMeterInformation))

    def _default_id_safe(self) -> str:
        try:
            return self._default_id()
        except Exception:
            return ""

    def _run(self):
        import comtypes
        try:
            comtypes.CoInitialize()
        except Exception as exc:
            logger.debug("CoInitialize: %s", exc)
        try:
            meter = self._make_meter()
        except Exception as exc:
            logger.warning("Измеритель динамиков не создался: %s", exc)
            self.available = False
            return
        misses = 0
        current_id = self._default_id_safe()
        checked_at = 0.0
        import time as _time
        while not self._stop.is_set():
            # Пользователь переключил вывод (наушники, HDMI), а старое
            # устройство живо: раньше замер оставался на нём, показывал 0,
            # и музыка из новых колонок снова уходила в Gemini как речь.
            now = _time.monotonic()
            if now - checked_at >= _DEVICE_CHECK_SEC:
                checked_at = now
                new_id = self._default_id_safe()
                if new_id and new_id != current_id:
                    try:
                        meter = self._make_meter()
                        current_id = new_id
                        logger.info("Устройство вывода сменилось — измеритель переключён")
                    except Exception as exc:
                        logger.debug("Переключение измерителя: %s", exc)
            try:
                self._peak = float(meter.GetPeakValue())
                misses = 0
            except Exception as exc:
                misses += 1
                if misses == 1:
                    logger.warning("Чтение уровня динамиков сорвалось: %s", exc)
                if misses > 20:
                    # Устройство сменилось — пересоздаём, а не глохнем навсегда.
                    try:
                        meter = self._make_meter()
                        misses = 0
                        logger.info("Измеритель динамиков пересоздан")
                    except Exception:
                        self._peak = 0.0
            self._stop.wait(self._poll)
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass
