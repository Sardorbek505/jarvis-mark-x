"""Прогрев кэша фраз стартует после подключения и не мешает голосовому циклу."""

import time
from unittest.mock import patch

import main as jarvis_main


class _Stub:
    _voice_warmup_started = False


def test_warmup_runs_in_background_and_only_once(monkeypatch, tmp_path):
    from core import voice_cache as vc
    monkeypatch.setattr(vc, "_voice_id", lambda: "voice-test")
    cache = vc.VoiceCache(tmp_path, sample_rate=24000)
    monkeypatch.setattr(jarvis_main, "get_voice_cache", lambda sample_rate=24000: cache)
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: "fish")
    monkeypatch.setattr(vc, "WARMUP_PAUSE_SEC", 0)

    async def fake_speak(text, sample_rate=24000):
        return b"\x01\x02" * 2400

    stub = _Stub()
    bound = jarvis_main.Jarvis._start_voice_warmup.__get__(stub, jarvis_main.Jarvis)
    with patch("telegram_bot.tts_fish.is_configured", return_value=True), \
         patch("telegram_bot.tts_fish.speak_pcm", fake_speak), \
         patch("core.voice_phrases.CANNED_PHRASES", ("Готово, сэр.", "Слушаю, сэр.")):
        started = time.perf_counter()
        bound()
        assert time.perf_counter() - started < 0.2, "прогрев обязан идти в фоне"
        bound()  # повторный вызов — не второй поток
        for _ in range(100):
            if cache.size() == 2:
                break
            time.sleep(0.02)

    assert cache.size() == 2
    assert stub._voice_warmup_started is True


def test_warmup_skipped_for_gemini_voice(monkeypatch):
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: "gemini")
    stub = _Stub()
    jarvis_main.Jarvis._start_voice_warmup.__get__(stub, jarvis_main.Jarvis)()
    assert stub._voice_warmup_started is False
