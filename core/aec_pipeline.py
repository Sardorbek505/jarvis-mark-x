"""JARVIS Mark X — Конвейер акустического эхоподавления (AEC / AFE Pipeline).

Вычитает звук собственных динамиков (опорный сигнал, Render Reference) из сигнала
микрофона: оценка задержки «динамик -> микрофон» по GCC-PHAT, адаптивный
блочный NLMS-фильтр и мягкое подавление остаточного эха.

Почему именно так — по следам замеров предыдущей версии:

  * Выравнивание. Опорный сигнал приходит в микрофон с задержкой в десятки
    миллисекунд (буферы WASAPI + воздух). Фильтр длиной 32 мс её не покрывает,
    поэтому задержка сначала компенсируется грубо — сдвигом окна, и только
    остаток дожимает адаптивный фильтр.

  * Индексация. Раньше окно опорного сигнала бралось от фиксированного
    `offset`, и на хвосте кадра срез выходил за границу истории; numpy молча
    возвращал укороченный массив, ветка «слишком коротко» копировала вход как
    есть — последние 512 семплов КАЖДОГО кадра шли мимо эхоподавления
    (замерено: RMS 8483 против 8491 на входе). Теперь окно строится от конца
    истории, а история заведомо длиннее, чем кадр + задержка + фильтр.

  * Скорость. Посемпловый цикл на Python стоил 25% ядра в реальном времени.
    Блочный NLMS считает тот же фильтр матричным умножением — те же обновления
    весов, но десятки numpy-операций на кадр вместо тысяч.

  * Двойной разговор. Пока говорит пользователь, адаптация замораживается:
    иначе фильтр начинает подстраиваться под живую речь и «съедает» её.
"""

import logging
import math
import numpy as np
from typing import Tuple

logger = logging.getLogger("jarvis-aec")

_INT16_SCALE = 32768.0

# Порог «в динамиках тишина» — ниже него эхо отсутствует и кадр отдаётся как есть.
_SILENT_REF_POWER = 1e-5

# Порог уверенного двойного разговора: во сколько раз остаточная энергия
# превышает оценку эха, чтобы счесть, что в кадре есть живая речь.
_DOUBLETALK_RATIO = 2.0

# С какого линейного ERLE считаем фильтр сошедшимся (4.0 ~ 6 дБ).
_CONVERGED_ERLE_LIN = 4.0

# Во сколько раз остаток должен превысить вход, чтобы счесть фильтр разошедшимся.
_DIVERGENCE_RATIO = 100.0

# Максимальный ERLE в отчёте: при идеальном сигнале остаток уходит в ноль,
# а log10(0) — не число.
_MAX_ERLE_DB = 60.0

# Через сколько кадров перепроверять задержку тракта после захвата.
# Она задаётся буферами звуковой подсистемы и расстоянием до колонок —
# величина постоянная, гонять GCC-PHAT на каждом кадре незачем.
_DELAY_RECHECK_FRAMES = 8


class AECPipeline:
    """Адаптивный акустический процессор эхоподавления и спектральной очистки."""

    def __init__(
        self,
        sample_rate: int = 16000,
        filter_length: int = 512,       # Длина адаптивного FIR фильтра (~32 мс)
        step_size: float = 0.5,         # Доля точного шага линейного поиска (<= 1)
        reg_epsilon: float = 1e-4,      # Регуляризация для защиты от деления на 0
        leakage: float = 1e-3,          # Утечка весов против разбегания на тонах
        max_delay_ms: float = 250.0,    # Максимальная компенсируемая задержка тракта
        block_size: int = 64,           # Размер под-блока адаптации (семплы)
        residual_suppression: bool = True,
    ):
        self.sample_rate = sample_rate
        self.filter_length = filter_length
        self.step_size = max(0.01, min(1.0, step_size))
        self.reg_epsilon = reg_epsilon
        self.leakage = max(0.0, min(0.1, leakage))
        self.block_size = max(16, block_size)
        self.residual_suppression = residual_suppression

        self.max_delay = int(sample_rate * max_delay_ms / 1000.0)

        # Веса адаптивного фильтра NLMS. w[k] умножает семпл окна с индексом k,
        # то есть отсчёт с лагом (filter_length - 1 - k).
        self._weights = np.zeros(filter_length, dtype=np.float32)

        # История опорного сигнала: должна вмещать кадр + задержку + фильтр,
        # иначе окно для первого семпла кадра уходит левее нуля.
        self._history_len = self.max_delay + filter_length + sample_rate // 4
        self._ref_history = np.zeros(self._history_len, dtype=np.float32)

        # Оценка задержки динамик -> микрофон (в семплах)
        self._estimated_delay = 0
        self._delay_locked = False

        # Сглаженное подавление (в разах) и признак сходимости фильтра
        self._erle_lin = 1.0
        self._converged = False

        self._frames_since_delay_check = 0

    # ── Оценка задержки ───────────────────────────────────────────────────────
    def _estimate_delay(self, mic: np.ndarray) -> int:
        """Задержка «динамик -> микрофон» в семплах по GCC-PHAT.

        Ищем сдвиг d, при котором mic[i] ≈ history[len(win) - n + i - d].
        PHAT-взвешивание убирает влияние спектра музыки: пик остаётся острым и
        на широкополосном материале, где обычная корреляция расплывается.
        """
        n = len(mic)
        win_len = min(len(self._ref_history), n + self.max_delay)
        win = self._ref_history[-win_len:]
        if win_len <= n:
            return self._estimated_delay

        n_fft = 1 << (win_len + n - 1).bit_length()
        f_win = np.fft.rfft(win, n=n_fft)
        f_mic = np.fft.rfft(mic, n=n_fft)

        cross = f_win * np.conj(f_mic)
        cross /= np.abs(cross) + 1e-9          # PHAT
        cc = np.fft.irfft(cross, n=n_fft)

        # cc[k] = сумма win[i + k] * mic[i]; допустимы k в [0, win_len - n]
        search = cc[: win_len - n + 1]
        if search.size == 0:
            return self._estimated_delay

        k_star = int(np.argmax(search))
        delay = win_len - n - k_star
        return int(max(0, min(self.max_delay, delay)))

    # ── Основной проход ───────────────────────────────────────────────────────
    def process_frame(self, mic_bytes: bytes, ref_bytes: bytes) -> Tuple[bytes, float]:
        """
        Обрабатывает один аудиокадр.

        Args:
            mic_bytes: PCM int16 байты с микрофона (16 кГц mono)
            ref_bytes: PCM int16 байты опорного сигнала колонок (16 кГц mono)

        Returns:
            clean_bytes: Очищенный PCM int16 поток
            erle_db: Echo Return Loss Enhancement (подавление в дБ)
        """
        if not mic_bytes:
            return b"", 0.0

        mic_arr = np.frombuffer(mic_bytes, dtype=np.int16).astype(np.float32) / _INT16_SCALE
        ref_arr = np.frombuffer(ref_bytes, dtype=np.int16).astype(np.float32) / _INT16_SCALE

        mic_power = float(np.mean(mic_arr ** 2))
        ref_power = float(np.mean(ref_arr ** 2)) if ref_arr.size else 0.0

        # Быстрый Bypass: если в колонках тишина, эхо отсутствует.
        # Историю всё равно двигаем, иначе после паузы выравнивание собьётся.
        if ref_power < _SILENT_REF_POWER:
            if ref_arr.size:
                self._push_reference(ref_arr)
            return mic_bytes, 0.0

        self._push_reference(ref_arr)

        n = len(mic_arr)
        if n + self._estimated_delay + self.filter_length > len(self._ref_history):
            self._grow_history(n)

        # Задержку ищем только на уверенном сигнале с обеих сторон.
        self._frames_since_delay_check += 1
        due = (not self._delay_locked
               or self._frames_since_delay_check >= _DELAY_RECHECK_FRAMES)
        if due and ref_power > 1e-3 and mic_power > 1e-3:
            self._frames_since_delay_check = 0
            delay = self._estimate_delay(mic_arr)
            if not self._delay_locked:
                self._estimated_delay = delay
                self._delay_locked = True
            else:
                self._estimated_delay = int(0.9 * self._estimated_delay + 0.1 * delay)

        aligned = self._aligned_window(n)
        if aligned is None:
            return mic_bytes, 0.0

        clean_arr = self._run_nlms(mic_arr, aligned)

        # Страховка: разошедшийся фильтр вбрасывает в микрофон мусор, который
        # хуже, чем отсутствие эхоподавления вовсе. Заметили — сбрасываем
        # адаптацию и отдаём кадр как есть.
        linear_power = float(np.mean(np.square(clean_arr, dtype=np.float64)))
        if not np.isfinite(linear_power) or linear_power > _DIVERGENCE_RATIO * mic_power:
            logger.warning("AEC: фильтр разошёлся — сброс адаптации")
            self.reset()
            return mic_bytes, 0.0

        # Сходимость оцениваем по ЛИНЕЙНОМУ остатку, до подавления остаточного
        # эха: иначе спектральная маска завышает ERLE и детектор двойного
        # разговора включается раньше, чем фильтр реально сошёлся.
        self._update_convergence(mic_power, linear_power)

        if self.residual_suppression:
            clean_arr = self._suppress_residual(clean_arr, mic_arr)

        clean_power = float(np.mean(np.square(clean_arr, dtype=np.float64)))
        if mic_power <= _SILENT_REF_POWER:
            erle_db = 0.0
        elif clean_power <= 0.0:
            erle_db = _MAX_ERLE_DB
        else:
            erle_db = min(_MAX_ERLE_DB, 10.0 * math.log10(mic_power / clean_power))

        clean_int16 = np.clip(clean_arr * _INT16_SCALE, -32768, 32767).astype(np.int16)
        return clean_int16.tobytes(), max(0.0, erle_db)

    # ── Внутреннее ────────────────────────────────────────────────────────────
    def _push_reference(self, ref_arr: np.ndarray):
        """Сдвигает кольцевую историю опорного сигнала на длину нового кадра."""
        n = len(ref_arr)
        if n >= len(self._ref_history):
            self._ref_history = ref_arr[-len(self._ref_history):].astype(np.float32).copy()
            return
        self._ref_history[:-n] = self._ref_history[n:]
        self._ref_history[-n:] = ref_arr

    def _grow_history(self, frame_len: int):
        """Увеличивает историю под длинный кадр, сохраняя уже накопленные данные."""
        new_len = frame_len + self.max_delay + self.filter_length + self.sample_rate // 4
        grown = np.zeros(new_len, dtype=np.float32)
        grown[-len(self._ref_history):] = self._ref_history
        self._ref_history = grown
        self._history_len = new_len

    def _aligned_window(self, n: int):
        """Матрица (n, filter_length) отсчётов опорного сигнала под каждый семпл кадра.

        Строка i — это окно фильтра для mic[i]: отсчёты истории, заканчивающиеся
        на позиции, куда с учётом задержки попал соответствующий семпл динамиков.
        """
        L = self.filter_length
        end = len(self._ref_history) - self._estimated_delay   # эксклюзивный конец
        start = end - n - L + 1
        if start < 0 or end > len(self._ref_history):
            return None

        seg = self._ref_history[start:end]
        if len(seg) != n + L - 1:
            return None

        view = np.lib.stride_tricks.sliding_window_view(seg, L)
        # Копия обязательна. Скользящее окно — это strided-представление с
        # перекрытием, BLAS с таким не работает и numpy пересобирает буфер на
        # КАЖДОМ умножении; на трёх умножениях за блок это давало три копии
        # вместо одной. Одна непрерывная матрица на кадр — и дальше срезы по
        # строкам достаются бесплатно.
        return np.ascontiguousarray(view)

    def _run_nlms(self, mic_arr: np.ndarray, aligned: np.ndarray) -> np.ndarray:
        """Блочный NLMS: выход фильтра — матричное умножение, веса правятся по под-блокам."""
        clean = np.empty_like(mic_arr)
        n = len(mic_arr)
        bs = self.block_size

        for start in range(0, n, bs):
            stop = min(start + bs, n)
            x_block = aligned[start:stop]                 # (b, L)
            d_block = mic_arr[start:stop]                 # (b,)

            echo_est = x_block @ self._weights            # (b,)
            err = d_block - echo_est
            clean[start:stop] = err

            if self._is_doubletalk(err, echo_est):
                continue

            # Нормируем на СРЕДНЮЮ мощность окна на семпл. Если поделить на
            # суммарную мощность блока, шаг оказывается занижен ровно в
            # block_size раз и фильтр не сходится: замерено 2.5 дБ подавления
            # вместо 120+ дБ на том же сигнале.
            # Шаг вдоль градиента с точным линейным поиском.
            #
            # Обычная нормировка NLMS (делить на энергию окна) на тональном
            # материале взрывается: у выдержанной ноты входная матрица блока
            # вырождена, строки почти коллинеарны, и b согласованных поправок
            # разом перелетают минимум — замерено переполнение float32 уже
            # внутри одного кадра. Здесь длина шага считается по фактическому
            # изменению выхода фильтра, поэтому поправка не может превысить
            # текущую ошибку ни при какой обусловленности сигнала.
            grad = x_block.T @ err                       # (L,)
            proj = x_block @ grad                        # (b,) — как сдвинется выход
            denom = float(np.dot(proj, proj))
            if denom <= 0.0:
                continue
            alpha = self.step_size * float(np.dot(err, proj)) / denom

            # Утечка сбрасывает медленный рост весов в нуль-пространстве входа.
            if self.leakage:
                self._weights *= (1.0 - self.leakage)
            self._weights += alpha * grad

        return clean

    def _is_doubletalk(self, err: np.ndarray, echo_est: np.ndarray) -> bool:
        """Звучит ли поверх эха живая речь — тогда адаптацию замораживаем.

        Проверку включаем только после сходимости фильтра: пока веса близки к
        нулю, остаток равен входу и любой такой детектор сработал бы на первом
        же блоке, навсегда заморозив адаптацию.
        """
        if not self._converged:
            return False
        echo_power = float(np.dot(echo_est, echo_est))
        if echo_power <= 0.0:
            return False
        return float(np.dot(err, err)) > _DOUBLETALK_RATIO * echo_power

    def _update_convergence(self, mic_power: float, linear_residual_power: float):
        """Сглаженно отслеживает подавление и поднимает флаг сходимости."""
        if mic_power <= 0.0 or linear_residual_power <= 0.0:
            return
        instant = mic_power / linear_residual_power
        self._erle_lin = 0.9 * self._erle_lin + 0.1 * instant
        self._converged = self._erle_lin >= _CONVERGED_ERLE_LIN

    def _suppress_residual(self, clean_arr: np.ndarray, mic_arr: np.ndarray) -> np.ndarray:
        """Мягкое подавление остаточного эха винеровской маской в частотной области.

        Линейный фильтр никогда не убирает эхо полностью: остаётся хвост от
        нелинейности динамика. Давим его по спектру, с полом усиления, — полное
        зануление резало бы и речь.
        """
        n = len(clean_arr)
        if n < 32:
            return clean_arr

        spec_clean = np.fft.rfft(clean_arr)
        spec_mic = np.fft.rfft(mic_arr)

        mag_clean = np.abs(spec_clean)
        mag_removed = np.abs(spec_mic - spec_clean)     # то, что вычел фильтр = оценка эха

        gain = mag_clean / (mag_clean + 0.35 * mag_removed + 1e-9)
        np.clip(gain, 0.1, 1.0, out=gain)

        return np.fft.irfft(spec_clean * gain, n=n).astype(np.float32)

    def reset(self):
        """Сброс адаптации: веса, история и оценка задержки."""
        self._weights[:] = 0.0
        self._ref_history[:] = 0.0
        self._estimated_delay = 0
        self._delay_locked = False
        self._erle_lin = 1.0
        self._converged = False
        self._frames_since_delay_check = 0
