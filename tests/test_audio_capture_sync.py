"""Выравнивание микрофона и опорного потока колонок по времени.

Регрессия: опорный буфер работал как очередь — микрофонный колбэк забирал
n байт С НАЧАЛА и удалял их, а обрезка переполнения выбрасывала тоже самое
старое. Потоки идут на независимых часах и разными блоками, поэтому очередь
неизбежно разъезжается:

  * loopback быстрее микрофона — буфер копится, обрезка режет старое, но
    читали как раз старое: опорный сигнал отставал вплоть до 4 секунд;
  * loopback медленнее — выдавалась тишина, и сдвиг закреплялся навсегда.

Ни то ни другое AEC компенсировать не может: он ищет задержку в пределах
250 мс. Ниже проверяется, что окно берётся по метке времени и рассинхрон
остаётся ограниченным.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audio_capture import TARGET_SAMPLE_RATE, AudioCaptureEngine

SR = TARGET_SAMPLE_RATE
BLOCK = 1024


def _block(index: int, n: int = BLOCK) -> bytes:
    """Опорный блок, целиком заполненный своим порядковым номером.

    Номер в значении семпла, а не нарастающая пила: пила за секунду вылезает
    за границу int16, а по константе позиция во времени читается так же ясно.
    """
    return np.full(n, index, dtype=np.int16).tobytes()


def _block_id(pcm: bytes) -> int:
    """Номер блока, с которого начинается выданное окно."""
    return int(np.frombuffer(pcm, dtype=np.int16)[0])


def test_ref_window_is_silent_before_any_reference():
    engine = AudioCaptureEngine()
    window = engine._ref_window(ts=1.0, n_bytes=BLOCK * 2)
    assert window == b"\x00" * (BLOCK * 2)


def test_ref_window_returns_the_matching_time_slice():
    """Кадр микрофона получает тот опорный участок, что звучал в это же время."""
    engine = AudioCaptureEngine()

    # Секунда опорного сигнала блоками по 1024 семпла, метка = конец блока.
    t = 0.0
    blocks = SR // BLOCK
    for i in range(blocks):
        t += BLOCK / SR
        engine._push_ref(_block(i), ts=t)

    # Микрофонный кадр приходит ровно тогда же, что и последний опорный блок,
    # и просит ровно один блок — значит должен получить именно последний.
    window = engine._ref_window(ts=t, n_bytes=BLOCK * 2)
    assert len(window) == BLOCK * 2
    assert _block_id(window) == blocks - 1


def test_ref_window_walks_back_in_time():
    """Кадр, пришедший позже, забирает более поздний участок опорного сигнала."""
    engine = AudioCaptureEngine()
    t = 0.0
    for i in range(2 * SR // BLOCK):
        t += BLOCK / SR
        engine._push_ref(_block(i), ts=t)

    recent = _block_id(engine._ref_window(ts=t, n_bytes=BLOCK * 2))
    older = _block_id(engine._ref_window(ts=t - 0.5, n_bytes=BLOCK * 2))

    assert older < recent, "окно не сдвигается назад во времени"
    # Полсекунды назад — это SR/2 семплов назад, с точностью до блока.
    moved_samples = (recent - older) * BLOCK
    assert abs(moved_samples - SR // 2) <= BLOCK


def test_reference_is_not_consumed_by_reads():
    """Чтение не должно опустошать буфер: иначе потоки снова станут очередью."""
    engine = AudioCaptureEngine()
    t = 0.0
    for i in range(SR // BLOCK):
        t += BLOCK / SR
        engine._push_ref(_block(i), ts=t)

    size_before = len(engine._ref_buffer)
    first = engine._ref_window(ts=t, n_bytes=BLOCK * 2)
    second = engine._ref_window(ts=t, n_bytes=BLOCK * 2)

    assert len(engine._ref_buffer) == size_before, "буфер вычерпывается чтением"
    assert first == second, "повторное чтение того же момента даёт другой результат"


def test_fast_reference_does_not_desynchronise_the_microphone():
    """Опорный поток быстрее микрофона — рассинхрон обязан остаться ограниченным.

    Это и есть сценарий, ломавший старую очередь: буфер переполнялся, обрезка
    выбрасывала самое старое, а микрофон читал именно оттуда и уезжал на
    секунды назад.
    """
    engine = AudioCaptureEngine()

    t = 0.0
    index = 0
    drift_blocks = []
    for step in range(400):
        # На каждый кадр микрофона приходит три блока опорного сигнала.
        for _ in range(3):
            t += BLOCK / SR
            engine._push_ref(_block(index), ts=t)
            index += 1

        got = _block_id(engine._ref_window(ts=t, n_bytes=BLOCK * 2))
        expected = index - 1                  # последний положенный блок
        if step > 5:                          # первые кадры — прогрев буфера
            drift_blocks.append(abs(got - expected))

    worst_samples = max(drift_blocks) * BLOCK
    worst_ms = worst_samples / SR * 1000.0
    assert worst_ms < 250.0, (
        "рассинхрон %.0f мс — AEC компенсирует не больше 250 мс" % worst_ms
    )


def test_ref_window_length_always_matches_the_request():
    """Сколько байт попросили — столько и вернулось, при любом положении окна."""
    engine = AudioCaptureEngine()
    engine._push_ref(_block(1), ts=1.0)

    for ts in (0.0, 0.5, 1.0, 1.5, 100.0):
        for n_bytes in (256, BLOCK * 2, BLOCK * 8):
            assert len(engine._ref_window(ts=ts, n_bytes=n_bytes)) == n_bytes
