"""Кэш готовых фраз: фиксированная реплика синтезируется Fish один раз.

До этого «Поставил на паузу, сэр» гонялось через Fish при каждой команде —
1–2 с днём, до 11 с ночью — ради фразы, которая никогда не меняется.
"""

import pytest

from core import voice_cache as vc


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(vc, "_voice_id", lambda: "voice-A")
    return vc.VoiceCache(tmp_path, sample_rate=24000)


PCM = b"\x01\x02" * 2400


def test_miss_then_hit(cache):
    assert cache.get("Поставил на паузу, сэр.") is None
    cache.put("Поставил на паузу, сэр.", PCM)
    assert cache.get("Поставил на паузу, сэр.") == PCM


def test_key_ignores_spacing_and_case_but_not_words(cache):
    cache.put("Поставил на паузу, сэр.", PCM)
    assert cache.get("  поставил на  паузу, сэр. ") == PCM
    assert cache.get("Поставил на паузу.") is None


def test_other_voice_does_not_see_the_entry(cache, tmp_path, monkeypatch):
    cache.put("Готово, сэр.", PCM)
    monkeypatch.setattr(vc, "_voice_id", lambda: "voice-B")
    other = vc.VoiceCache(tmp_path, sample_rate=24000)
    assert other.get("Готово, сэр.") is None


def test_other_sample_rate_does_not_see_the_entry(cache, tmp_path):
    cache.put("Готово, сэр.", PCM)
    assert vc.VoiceCache(tmp_path, sample_rate=16000).get("Готово, сэр.") is None


def test_long_phrases_are_not_cached(cache):
    long_text = "Сэр, " + "очень " * 40 + "длинная фраза."
    cache.put(long_text, PCM)
    assert cache.get(long_text) is None
    assert cache.size() == 0


def test_empty_or_tiny_pcm_is_not_cached(cache):
    cache.put("Готово, сэр.", b"")
    cache.put("Ясно, сэр.", b"\x00" * 10)
    assert cache.size() == 0


def test_survives_new_instance(cache, tmp_path):
    cache.put("Готово, сэр.", PCM)
    again = vc.VoiceCache(tmp_path, sample_rate=24000)
    assert again.get("Готово, сэр.") == PCM


def test_lru_eviction_keeps_recent(cache, monkeypatch):
    monkeypatch.setattr(vc, "MAX_ENTRIES", 3)
    for i in range(3):
        cache.put(f"Фраза {i}.", PCM)
        cache._touch_clock += 1  # детерминированный порядок «свежести»
    cache.get("Фраза 0.")       # освежили нулевую
    cache._touch_clock += 1
    cache.put("Фраза 3.", PCM)   # вытесняется самая давняя — «Фраза 1»
    assert cache.size() == 3
    assert cache.get("Фраза 0.") == PCM
    assert cache.get("Фраза 1.") is None
    assert cache.get("Фраза 3.") == PCM


def test_corrupt_file_is_a_miss_not_a_crash(cache, tmp_path):
    cache.put("Готово, сэр.", PCM)
    path = next(tmp_path.glob("*.wav"))
    path.write_bytes(b"garbage")
    assert cache.get("Готово, сэр.") is None


# ── Прогрев ──────────────────────────────────────────────────────────────────


def test_warmup_synthesizes_only_misses(cache):
    cache.put("Готово, сэр.", PCM)
    synthesized = []

    def synth(text):
        synthesized.append(text)
        return PCM

    done = cache.warmup(["Готово, сэр.", "Слушаю, сэр.", "Да, сэр."], synth, pause_sec=0)

    assert synthesized == ["Слушаю, сэр.", "Да, сэр."]
    assert done == 2
    assert cache.get("Да, сэр.") == PCM


def test_warmup_stops_after_repeated_failures(cache):
    attempts = []

    def broken(text):
        attempts.append(text)
        return None

    phrases = [f"Фраза {i}." for i in range(10)]
    done = cache.warmup(phrases, broken, pause_sec=0)

    assert done == 0
    assert len(attempts) == vc.WARMUP_MAX_FAILURES, "после серии отказов прогрев обязан остановиться"


def test_warmup_survives_exception_in_synth(cache):
    calls = []

    def flaky(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("HTTP 429")
        return PCM

    done = cache.warmup(["Раз.", "Два."], flaky, pause_sec=0)
    assert done == 1
    assert cache.get("Два.") == PCM
