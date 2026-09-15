import asyncio
import threading
from types import SimpleNamespace
import pytest

import main as jarvis_main
from core.latency import LatencyTracker


class _StreamingStub:
    def __init__(self, loop):
        self._loop = loop
        self._speaking_lock = threading.Lock()
        self._active_synth_tasks = 0
        self._is_speaking = False
        self._speech_epoch = 0
        self._current_generation_id = 1
        self._active_speech_generation_id = 1
        self._current_utterance_id = "utt_1"
        self._active_speech_utterance_id = "utt_1"
        self._interrupted_turn = False
        self._active_speech_tasks = set()
        self.audio_in_queue = asyncio.Queue()
        self._streaming_speech_active = False
        self._streaming_queue = None
        self._streamed_chunks_indices = set()
        self._speech_prefetch = None
        self._latency = LatencyTracker(enabled=False)
        self.logs = []
        self.ui = SimpleNamespace(write_log=self.logs.append, set_state=lambda s: None)
        self._pending_gemini_turn = None

        # Bind methods from Jarvis
        self.set_speaking = jarvis_main.Jarvis.set_speaking.__get__(self, jarvis_main.Jarvis)
        self._take_prefetched_speech = jarvis_main.Jarvis._take_prefetched_speech.__get__(self, jarvis_main.Jarvis)
        self._maybe_stream_speech = jarvis_main.Jarvis._maybe_stream_speech.__get__(self, jarvis_main.Jarvis)
        self._run_streaming_speech = jarvis_main.Jarvis._run_streaming_speech.__get__(self, jarvis_main.Jarvis)
        self._iter_fragment_audio = jarvis_main.Jarvis._iter_fragment_audio.__get__(self, jarvis_main.Jarvis)
        self._push_fragment_audio = jarvis_main.Jarvis._push_fragment_audio.__get__(self, jarvis_main.Jarvis)
        self._abort_playback = jarvis_main.Jarvis._abort_playback.__get__(self, jarvis_main.Jarvis)
        self._clear_audio_in_queue = jarvis_main.Jarvis._clear_audio_in_queue.__get__(self, jarvis_main.Jarvis)


@pytest.mark.asyncio
async def test_streaming_speech_starts_on_first_chunk(monkeypatch):
    """Стриминг TTS запускается и отдаёт аудио первого предложения до окончания генерации всего ответа."""
    loop = asyncio.get_running_loop()
    stub = _StreamingStub(loop)

    fake_pcm_data = b"\x01\x02" * 1024
    spoken_fragments = []

    async def fake_speak_pcm(fragment, sample_rate=24000):
        spoken_fragments.append(fragment)
        yield fake_pcm_data

    from telegram_bot import tts_fish
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: "fish")
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "stream_pcm", fake_speak_pcm)

    # 1. Поступает начало фразы: 1 неполное предложение -> стриминг не должен запускаться
    stub._maybe_stream_speech("Здравствуйте, сэр")
    assert not stub._streaming_speech_active
    assert stub._streaming_queue is None

    # 2. Появляется второе предложение -> первое предложение зафиксировано и стриминг стартует
    text_two_sentences = "Здравствуйте, сэр. Системы запущены в штатном режиме."
    stub._maybe_stream_speech(text_two_sentences)

    assert stub._streaming_speech_active
    assert stub._streaming_queue is not None
    assert 0 in stub._streamed_chunks_indices

    # Даём фоновой таске прокрутиться и синтезировать чанк 0 (первый импорт
    # edge_tts в процессе может занять больше фиксированных 50 мс)
    for _ in range(100):
        if not stub.audio_in_queue.empty():
            break
        await asyncio.sleep(0.02)

    assert "Здравствуйте, сэр." in spoken_fragments
    assert not stub.audio_in_queue.empty(), "Аудио первого предложения должно поступить в audio_in_queue"
    assert stub._is_speaking, "Флаг говорения должен быть активен"

    # 3. Завершение хода (turn_complete) -> отправляем финальный чанк и закрываем маркер None
    final_chunks = jarvis_main._split_for_speech(text_two_sentences)
    for idx, ch in enumerate(final_chunks):
        if idx not in stub._streamed_chunks_indices:
            stub._streamed_chunks_indices.add(idx)
            stub._streaming_queue.put_nowait(ch)
    stub._streaming_queue.put_nowait(None)

    # Ждём завершения синтеза второго предложения
    for _ in range(20):
        if len(spoken_fragments) >= 2:
            break
        await asyncio.sleep(0.02)

    assert len(spoken_fragments) == 2
    assert "Системы запущены в штатном режиме." in spoken_fragments[1]


@pytest.mark.asyncio
async def test_streaming_speech_aborts_on_barge_in(monkeypatch):
    """Прерывание пользователя (Barge-In) мгновенно останавливает стриминг и очищает очередь."""
    loop = asyncio.get_running_loop()
    stub = _StreamingStub(loop)

    async def slow_speak_pcm(fragment, sample_rate=24000):
        await asyncio.sleep(1.0)
        yield b"\x01\x02" * 1024

    from telegram_bot import tts_fish
    monkeypatch.setattr(jarvis_main, "get_voice_provider", lambda: "fish")
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "stream_pcm", slow_speak_pcm)

    stub._maybe_stream_speech("Здравствуйте, сэр. Я вас внимательно слушаю.")
    assert stub._streaming_speech_active
    assert len(stub._active_speech_tasks) > 0

    # Прерывание
    stub._abort_playback()

    assert not stub._streaming_speech_active
    assert stub._streaming_queue is None
    assert len(stub._active_speech_tasks) == 0
    assert stub.audio_in_queue.empty()
