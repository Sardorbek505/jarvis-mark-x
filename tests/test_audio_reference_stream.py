"""Опорный сигнал для AEC: непрерывность чтения и качество ресэмплинга.

Обе вещи новые и до сих пор проверялись только на слух на живой машине.
Поблочный linspace-ресэмплинг давал копию музыки с корреляцией 0.29 к
оригиналу, а выборка опорного окна по часам рвала поток на каждом кадре —
адаптивный фильтр не сходился ни разу.
"""

import numpy as np
import pytest

from core.audio_capture import TARGET_SAMPLE_RATE, AudioCaptureEngine, _StreamResampler

FRAME_BYTES = 1024 * 2  # 1024 семпла int16


def _engine() -> AudioCaptureEngine:
    return AudioCaptureEngine()


def _ramp(n_samples: int, start: int = 0) -> bytes:
    """Пила — по ней видно разрывы и повторы. Значения заворачиваются в int16."""
    vals = np.arange(start, start + n_samples, dtype=np.int64)
    return (((vals + 32768) % 65536) - 32768).astype(np.int16).tobytes()


def test_reference_reads_are_contiguous_despite_jittery_timestamps():
    """Дрожь меток времени не должна рвать опорный поток.

    Колбэки приходят неравномерно, и выборка строго по часам выдавала то
    перекрытие, то дыру. Читаем подряд — значит склейка двух окон обязана
    продолжать пилу без пропусков и повторов.
    """
    eng = _engine()
    total = 0
    for block in range(8):
        eng._push_ref(_ramp(1024, start=total), ts=0.064 * (block + 1))
        total += 1024

    jittery = (0.30, 0.33, 0.31, 0.35)
    chunks = [eng._ref_window_continuous(ts, FRAME_BYTES) for ts in jittery]
    joined = np.frombuffer(b"".join(chunks), dtype=np.int16)

    steps = np.diff(joined.astype(np.int32))
    assert np.all(steps == 1), f"опорный сигнал рвётся: шаги {sorted(set(steps.tolist()))}"


def test_reference_resyncs_when_clock_drifts_far():
    """Уход больше REF_RESYNC_SEC — перескок на метку времени, а не накопление долга."""
    eng = _engine()
    for block in range(60):
        eng._push_ref(_ramp(1024, start=block * 1024), ts=0.064 * (block + 1))

    near = eng._ref_window_continuous(3.84, FRAME_BYTES)
    cursor_before = eng._ref_cursor
    # Прыжок на секунду назад — это больше порога ресинхронизации
    eng._ref_window_continuous(2.84, FRAME_BYTES)

    assert near != b"\x00" * FRAME_BYTES
    assert eng._ref_cursor < cursor_before, "курсор не перескочил на новую метку"


def test_reference_window_is_silence_before_any_audio():
    """Пока колонки молчали, опорное окно — тишина нужной длины, а не пусто."""
    eng = _engine()
    assert eng._ref_window_continuous(1.0, FRAME_BYTES) == b"\x00" * FRAME_BYTES


def test_stream_resampler_preserves_tone_shape():
    """Ресэмплер обязан сохранять сигнал: иначе AEC нечего вычитать.

    Гоняем 1 кГц блоками, как приходит loopback, и сверяем с эталоном,
    посчитанным сразу на 16 кГц.
    """
    soxr = pytest.importorskip("soxr", reason="без soxr путь заведомо грубый")
    assert soxr

    orig_sr = 48000
    duration = 0.5
    t = np.arange(int(orig_sr * duration)) / orig_sr
    loud = (np.sin(2 * np.pi * 1000 * t) * 12000).astype(np.int16)

    resampler = _StreamResampler(orig_sr)
    out = [resampler.process(loud[i:i + 1024].astype(np.float32)) for i in range(0, len(loud), 1024)]
    got = np.concatenate(out).astype(np.float64)

    t16 = np.arange(int(TARGET_SAMPLE_RATE * duration)) / TARGET_SAMPLE_RATE
    want = np.sin(2 * np.pi * 1000 * t16) * 12000

    n = min(len(got), len(want))
    # Ресэмплер даёт групповую задержку — сравниваем по лучшему сдвигу
    best = max(
        abs(np.corrcoef(got[shift:n], want[:n - shift])[0, 1])
        for shift in range(0, 40)
    )
    assert best > 0.95, f"сигнал искажён, корреляция {best:.2f}"


def test_stream_resampler_is_transparent_at_target_rate():
    """Loopback уже на 16 кГц — трогать сигнал незачем."""
    resampler = _StreamResampler(TARGET_SAMPLE_RATE)
    block = np.arange(1024, dtype=np.float32)
    assert np.array_equal(resampler.process(block), block.astype(np.int16))
