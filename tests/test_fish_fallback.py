"""Падение Fish не должно стоить таймаута на каждом предложении.

У запроса к Fish таймаут 60 секунд (tts_fish._TIMEOUT_SEC). Пока живость
провайдера проверялась внутри каждого фрагмента, ответ из четырёх
предложений при лежащем Fish молчал до четырёх минут: каждый кусок заново
убеждался в том, что уже выяснил предыдущий.

Здесь проверяется, что вывод делается один раз за ответ.
"""
import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as jarvis_main
from core.latency import LatencyTracker

_TEXT = ("Первое предложение достаточной длины для отдельного куска. "
         "Второе предложение такой же длины для отдельного куска. "
         "Третье предложение такой же длины для отдельного куска.")


class _Stub:
    """Минимальный носитель состояния для _speak_fish."""

    def __init__(self):
        self._speaking_lock = threading.Lock()
        self._active_synth_tasks = 0
        self._is_speaking = False
        self.audio_in_queue = asyncio.Queue()
        self.logs: list[str] = []
        self.ui = SimpleNamespace(write_log=self.logs.append)
        self._latency = LatencyTracker(enabled=False)

    def set_speaking(self, value: bool) -> None:
        self._is_speaking = value

    # Общий тракт озвучки фрагмента — тот же, что у боевого Jarvis
    _iter_fragment_audio = jarvis_main.Jarvis._iter_fragment_audio
    _push_fragment_audio = jarvis_main.Jarvis._push_fragment_audio


def _speak(stub: _Stub, text: str) -> None:
    bound = jarvis_main.Jarvis._speak_fish.__get__(stub, jarvis_main.Jarvis)
    asyncio.run(bound(text))


def _wire(monkeypatch, *, configured: bool, fish_pcm: bytes | None):
    """Подменяет обоих провайдеров и возвращает списки вызовов."""
    from telegram_bot import tts_edge, tts_fish

    fish_calls: list[str] = []
    edge_calls: list[str] = []

    async def fake_fish(fragment, sample_rate=24000):
        fish_calls.append(fragment)
        return fish_pcm

    async def fake_fish_stream(fragment, sample_rate=24000):
        fish_calls.append(fragment)
        if fish_pcm:
            yield fish_pcm

    async def fake_edge(fragment, sample_rate=24000):
        edge_calls.append(fragment)
        return b"\x00\x01" * 600

    monkeypatch.setattr(tts_fish, "is_configured", lambda: configured)
    monkeypatch.setattr(tts_fish, "speak_pcm", fake_fish)
    monkeypatch.setattr(tts_fish, "stream_pcm", fake_fish_stream)
    monkeypatch.setattr(tts_edge, "speak_pcm", fake_edge)
    return fish_calls, edge_calls


def test_dead_fish_is_probed_once_per_answer(monkeypatch):
    """Отказавший Fish опрашивается один раз, остаток договаривает Edge."""
    fish_calls, edge_calls = _wire(monkeypatch, configured=True, fish_pcm=None)
    expected = len(jarvis_main._split_for_speech(_TEXT))
    assert expected > 1, "текст должен резаться минимум на два куска"

    _speak(_Stub(), _TEXT)

    assert len(fish_calls) == 1
    assert len(edge_calls) == expected


def test_unconfigured_fish_is_not_called_at_all(monkeypatch):
    """Без ключа Fish не дёргается вовсе — сразу Edge."""
    fish_calls, edge_calls = _wire(monkeypatch, configured=False, fish_pcm=None)

    _speak(_Stub(), _TEXT)

    assert fish_calls == []
    assert len(edge_calls) == len(jarvis_main._split_for_speech(_TEXT))


def test_live_fish_never_falls_back(monkeypatch):
    """Пока Fish отвечает, Edge не зовут — голос остаётся джарвисовским."""
    fish_calls, edge_calls = _wire(
        monkeypatch, configured=True, fish_pcm=b"\x00\x01" * 600)

    _speak(_Stub(), _TEXT)

    assert len(fish_calls) == len(jarvis_main._split_for_speech(_TEXT))
    assert edge_calls == []


# ── Хедж по первому звуку ─────────────────────────────────────────────────────
#
# 15.09.2026 бесплатный Fish отдавал первый байт через 11 с — ответы не
# дозвучивали вовсе. Если за JARVIS_TTS_FIRST_AUDIO_TIMEOUT Fish молчит,
# параллельно поднимается Edge; кто первый — тот говорит.


def _wire_hedge(monkeypatch, *, fish_delay: float, edge_delay: float):
    from telegram_bot import tts_edge, tts_fish

    calls = {"fish": 0, "edge": 0, "fish_closed": False}

    async def slow_fish(fragment, sample_rate=24000):
        calls["fish"] += 1
        try:
            await asyncio.sleep(fish_delay)
            yield b"\x0f\x0f" * 600
        finally:
            calls["fish_closed"] = True

    async def edge(fragment, sample_rate=24000):
        calls["edge"] += 1
        await asyncio.sleep(edge_delay)
        return b"\x0e\x0e" * 600

    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "stream_pcm", slow_fish)
    monkeypatch.setattr(tts_edge, "speak_pcm", edge)
    monkeypatch.setattr(jarvis_main, "_TTS_FIRST_AUDIO_TIMEOUT_SEC", 0.05)
    return calls


def _drain(stub: _Stub) -> bytes:
    out = b""
    while not stub.audio_in_queue.empty():
        out += stub.audio_in_queue.get_nowait()
    return out


def test_slow_fish_is_hedged_by_edge(monkeypatch):
    calls = _wire_hedge(monkeypatch, fish_delay=1.0, edge_delay=0.01)
    stub = _Stub()

    _speak(stub, "Все системы в норме, сэр.")

    audio = _drain(stub)
    assert audio.startswith(b"\x0e\x0e"), "фразу должен был озвучить Edge"
    assert calls["edge"] == 1
    assert calls["fish_closed"], "поток Fish не закрыт после проигрыша"
    assert any("Edge" in line for line in stub.logs)


def test_fast_fish_keeps_its_voice_and_cancels_edge(monkeypatch):
    calls = _wire_hedge(monkeypatch, fish_delay=0.0, edge_delay=1.0)
    stub = _Stub()

    _speak(stub, "Все системы в норме, сэр.")

    audio = _drain(stub)
    assert audio.startswith(b"\x0f\x0f"), "голос должен остаться Fish"
    assert calls["edge"] == 0, "Edge поднят, хотя Fish ответил вовремя"


def test_fish_wins_race_after_timeout_if_it_answers_first(monkeypatch):
    calls = _wire_hedge(monkeypatch, fish_delay=0.08, edge_delay=1.0)
    stub = _Stub()

    _speak(stub, "Все системы в норме, сэр.")

    audio = _drain(stub)
    assert audio.startswith(b"\x0f\x0f"), "Fish опоздал к таймауту, но ответил раньше Edge — говорить должен он"
    assert calls["edge"] == 1


def test_slow_prefetched_fish_is_hedged_too(monkeypatch):
    """Первая фраза, заказанная наперёд, ждёт Fish тем же таймаутом, а не вечно.

    Стенд 15.09.2026: стрим уже хеджировался, а предзаказ висел 13 с.
    """
    calls = _wire_hedge(monkeypatch, fish_delay=1.0, edge_delay=0.01)
    from telegram_bot import tts_fish

    async def slow_full(fragment, sample_rate=24000):
        await asyncio.sleep(1.0)
        return b"\x0f\x0f" * 600

    monkeypatch.setattr(tts_fish, "speak_pcm", slow_full)
    stub = _Stub()
    stub._take_prefetched_speech = jarvis_main.Jarvis._take_prefetched_speech.__get__(stub, jarvis_main.Jarvis)

    async def run():
        stub._speech_prefetch = ("Все системы в норме, сэр.", asyncio.create_task(slow_full("x")))
        bound = jarvis_main.Jarvis._speak_fish.__get__(stub, jarvis_main.Jarvis)
        await bound("Все системы в норме, сэр.")

    asyncio.run(run())

    audio = _drain(stub)
    assert audio.startswith(b"\x0e\x0e"), "предзаказ Fish должен был уступить Edge по таймауту"
    assert calls["edge"] == 1


def test_empty_edge_hedge_falls_back_to_fish(monkeypatch):
    """Edge выиграл гонку, но вернул пустоту (NoAudioReceived) — ждём Fish, а не молчим."""
    calls = _wire_hedge(monkeypatch, fish_delay=0.15, edge_delay=0.01)
    from telegram_bot import tts_edge

    async def empty_edge(fragment, sample_rate=24000):
        calls["edge"] += 1
        return None

    monkeypatch.setattr(tts_edge, "speak_pcm", empty_edge)
    stub = _Stub()

    _speak(stub, "Все системы в норме, сэр.")

    audio = _drain(stub)
    assert audio.startswith(b"\x0f\x0f"), "Edge промолчал — фразу обязан договорить Fish"
    assert calls["edge"] == 1


# ── Кэш готовых фраз в тракте озвучки ─────────────────────────────────────────


def _wire_cache(monkeypatch, tmp_path):
    from core import voice_cache as vc
    monkeypatch.setattr(vc, "_voice_id", lambda: "voice-test")
    cache = vc.VoiceCache(tmp_path, sample_rate=24000)
    monkeypatch.setattr(jarvis_main, "get_voice_cache", lambda sample_rate=24000: cache)
    return cache


def test_cached_phrase_sounds_without_calling_fish(monkeypatch, tmp_path):
    cache = _wire_cache(monkeypatch, tmp_path)
    cache.put("Поставил на паузу, сэр.", b"\x0c\x0c" * 2400)
    fish_calls, edge_calls = _wire(monkeypatch, configured=True, fish_pcm=b"\x0f\x0f" * 2400)
    stub = _Stub()

    _speak(stub, "Поставил на паузу, сэр.")

    assert _drain(stub).startswith(b"\x0c\x0c"), "фраза должна прозвучать из кэша"
    assert fish_calls == [] and edge_calls == []


def test_fish_result_is_written_to_cache(monkeypatch, tmp_path):
    cache = _wire_cache(monkeypatch, tmp_path)
    _wire(monkeypatch, configured=True, fish_pcm=b"\x0f\x0f" * 2400)
    stub = _Stub()

    _speak(stub, "Готово, сэр.")

    assert cache.get("Готово, сэр.") == b"\x0f\x0f" * 2400


def test_edge_voice_is_never_cached(monkeypatch, tmp_path):
    cache = _wire_cache(monkeypatch, tmp_path)
    _wire(monkeypatch, configured=True, fish_pcm=None)  # Fish молчит → Edge
    stub = _Stub()

    _speak(stub, "Готово, сэр.")

    assert cache.get("Готово, сэр.") is None


def test_interrupted_fish_stream_is_not_cached(monkeypatch, tmp_path):
    """Половина фразы в кэше хуже, чем ничего."""
    cache = _wire_cache(monkeypatch, tmp_path)
    from telegram_bot import tts_edge, tts_fish

    async def two_chunks(fragment, sample_rate=24000):
        yield b"\x0f\x0f" * 2400
        await asyncio.sleep(0)
        yield b"\x0f\x0f" * 2400

    async def edge(fragment, sample_rate=24000):
        return None

    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "stream_pcm", two_chunks)
    monkeypatch.setattr(tts_edge, "speak_pcm", edge)

    class _Interrupting(_Stub):
        pushes = 0

        def __init__(self):
            super().__init__()
            self._speech_epoch = 0
            self.audio_in_queue = _CountingQueue(self)

    class _CountingQueue(asyncio.Queue):
        def __init__(self, owner):
            super().__init__()
            self.owner = owner

        def put_nowait(self, item):
            super().put_nowait(item)
            self.owner._speech_epoch += 1  # перебили после первого куска

    stub = _Interrupting()
    _speak(stub, "Готово, сэр.")

    assert cache.get("Готово, сэр.") is None
