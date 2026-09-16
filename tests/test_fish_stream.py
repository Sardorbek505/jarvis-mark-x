"""Потоковый приём звука Fish: сэмплы отдаются по мере прихода, а не файлом.

Замер 15.09.2026: на фразе 116 символов первый байт приходил через 4.2 с, весь
файл — через 7.3 с; speak_pcm заставлял ждать всё.
"""

import asyncio
import io

import pytest

from telegram_bot import tts_fish


class _FakeResponse(io.BytesIO):
    """urllib-ответ: контекстный менеджер + read(n)."""


def _wav(pcm: bytes, junk_chunk: bool = False) -> bytes:
    header = b"RIFF" + (36 + len(pcm)).to_bytes(4, "little") + b"WAVE"
    header += b"fmt " + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00" + (24000).to_bytes(4, "little")
    header += (48000).to_bytes(4, "little") + b"\x02\x00\x10\x00"
    if junk_chunk:
        header += b"LIST" + (4).to_bytes(4, "little") + b"data"  # 'data' внутри чужого чанка не считается началом
    header += b"data" + len(pcm).to_bytes(4, "little")
    return header + pcm


def _run_stream(monkeypatch, body: bytes, read_bytes: int = 7):
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "_open_response", lambda *a, **k: _FakeResponse(body))
    monkeypatch.setattr(tts_fish, "_STREAM_READ_BYTES", read_bytes)

    async def collect():
        return [c async for c in tts_fish.stream_pcm("Все системы в норме, сэр.", 24000)]

    return asyncio.run(collect())


def test_stream_strips_header_and_keeps_every_sample(monkeypatch):
    pcm = bytes(range(256)) * 3
    chunks = _run_stream(monkeypatch, _wav(pcm))

    assert len(chunks) > 1, "поток пришёл одним куском — стриминга нет"
    assert b"".join(chunks) == pcm
    assert all(len(c) % 2 == 0 for c in chunks), "кусок рвёт int16-сэмпл пополам"


def test_stream_walks_riff_chunks_instead_of_searching_for_data_string(monkeypatch):
    """Служебный чанк LIST с текстом 'data' внутри не считается началом сэмплов."""
    pcm = b"" * 100
    chunks = _run_stream(monkeypatch, _wav(pcm, junk_chunk=True), read_bytes=64)
    assert b"".join(chunks) == pcm


def test_pcm_from_wav_walks_riff_chunks():
    pcm = b"" * 50
    assert tts_fish._pcm_from_wav(_wav(pcm, junk_chunk=True)) == pcm
    assert tts_fish._pcm_from_wav(b"not a wav at all") is None


def test_stream_yields_nothing_when_fish_fails(monkeypatch):
    def boom(*a, **k):
        raise OSError("timed out")

    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "_open_response", boom)

    async def collect():
        return [c async for c in tts_fish.stream_pcm("текст", 24000)]

    assert asyncio.run(collect()) == []


def test_stream_rejects_non_wav_body(monkeypatch):
    chunks = _run_stream(monkeypatch, b'{"error":"quota"}' + b"\x00" * 100)
    assert chunks == []


def test_stream_is_silent_when_not_configured(monkeypatch):
    monkeypatch.setattr(tts_fish, "is_configured", lambda: False)

    async def collect():
        return [c async for c in tts_fish.stream_pcm("текст", 24000)]

    assert asyncio.run(collect()) == []


@pytest.mark.asyncio
async def test_consumer_gets_first_chunk_before_download_ends(monkeypatch):
    """Первый кусок доходит до потребителя, пока сеть ещё отдаёт остальное."""
    gate = asyncio.Event()
    loop = asyncio.get_running_loop()

    class _SlowResponse:
        def __init__(self):
            self.parts = [_wav(b"\x00\x01" * 2048)[i:i + 512] for i in range(0, 4096 + 44, 512)]
            self.i = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            if self.i == 2:
                asyncio.run_coroutine_threadsafe(gate.wait(), loop).result(timeout=5)
            if self.i >= len(self.parts):
                return b""
            part = self.parts[self.i]
            self.i += 1
            return part

    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    monkeypatch.setattr(tts_fish, "_open_response", lambda *a, **k: _SlowResponse())
    monkeypatch.setattr(tts_fish, "_STREAM_READ_BYTES", 512)

    gen = tts_fish.stream_pcm("текст", 24000)
    first = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert first, "первый кусок не пришёл, пока скачивание не закончено"
    gate.set()
    rest = [c async for c in gen]
    assert rest
