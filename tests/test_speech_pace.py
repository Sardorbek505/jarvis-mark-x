"""Темп речи: тишина по краям куска срезается, длинные паузы внутри сжимаются,
короткие (смычка согласной, пауза между словами) не трогаются, между кусками —
своя короткая пауза."""
import numpy as np

from core import speech_pace as P

R = 24000


def tone(sec, amp=8000):
    t = np.arange(int(R * sec)) / R
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def quiet(sec, noise=20):
    return (np.random.default_rng(1).normal(0, noise, int(R * sec))).astype(np.int16)


def dur(pcm: bytes) -> float:
    return len(pcm) / 2 / R


def test_edges_trimmed_and_own_pause_added():
    clip = np.concatenate([quiet(0.45), tone(1.0), quiet(0.7)]).tobytes()
    out = P.tighten(clip, R, sentence_end=True)
    expect = P.KEEP_LEAD_SEC + 1.0 + P.KEEP_TAIL_SEC + P.SENTENCE_PAUSE_SEC
    assert abs(dur(out) - expect) < 0.03
    assert abs(dur(P.tighten(clip, R, sentence_end=False)) - (expect - P.SENTENCE_PAUSE_SEC + P.CLAUSE_PAUSE_SEC)) < 0.03


def test_long_inner_pause_shortened_short_ones_kept():
    word_gap = np.concatenate([tone(0.4), quiet(0.08), tone(0.4)])             # смычка / пробел — не трогать
    clip = np.concatenate([word_gap, quiet(0.9), tone(0.5)]).tobytes()         # «…, …» — затянутая пауза
    out = np.frombuffer(P.tighten(clip, R, sentence_end=False), dtype=np.int16)
    # звук от самого начала до конца — запаса по краям нет; 0.9 с паузы → MAX_INNER_PAUSE_SEC
    assert abs(dur(out.tobytes()) - (0.88 + P.MAX_INNER_PAUSE_SEC + 0.5 + P.CLAUSE_PAUSE_SEC)) < 0.02
    src = np.frombuffer(clip, dtype=np.int16)
    assert (np.abs(out) > 4000).sum() == (np.abs(src) > 4000).sum()            # звук весь на месте


def test_silence_and_empty_untouched():
    s = quiet(0.5).tobytes()
    assert P.tighten(s, R) == s and P.tighten(b"", R) == b""


def test_sentence_end():
    assert P.ends_sentence("Готово, сэр.") and P.ends_sentence("Правда? ") and not P.ends_sentence("Итак,")
