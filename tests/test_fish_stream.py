"""Fish потоком: звук играет, пока остальное ещё синтезируется (S2.1 Pro отдаёт
первый звук за ~70 мс у себя — раньше мы ждали файл целиком, ~1 с на кусок)."""
import asyncio
import io
import struct
import threading
from types import SimpleNamespace

import numpy as np
import pytest

import main as jarvis_main
from core import speech_pace as P
from core.latency import LatencyTracker
from telegram_bot import tts_fish


class _Resp(io.BytesIO):
    def __init__(self, data, block=7):
        super().__init__(data)
        self.block = block

    def read1(self, n=-1):
        return self.read(min(n, self.block))


def _collect(text="Привет, сэр."):
    async def go():
        return [c async for c in tts_fish.stream_pcm(text, 24000)]
    return asyncio.run(go())


@pytest.fixture
def fish(monkeypatch):
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    sent = {}

    def use(data, block=7, exc=None):
        def fake_open(text, fmt, latency, rate):
            sent.update(fmt=fmt, latency=latency, rate=rate)
            if exc:
                raise exc
            return _Resp(data, block)
        monkeypatch.setattr(tts_fish, "_open", fake_open)
        return sent
    return use


def test_raw_pcm_streams_in_whole_samples(fish):
    pcm = bytes(range(256)) * 8
    sent = fish(pcm, block=5)                               # куски нечётной длины
    chunks = _collect()
    assert b"".join(chunks) == pcm and len(chunks) > 10 and all(len(c) % 2 == 0 for c in chunks)
    assert sent == {"fmt": "pcm", "latency": "low", "rate": 24000}          # первый звук важнее


def test_wav_header_split_across_chunks_is_removed(fish):
    body = b"\x10\x20" * 400
    wav = b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + b"\x00" * 20 + b"LIST\x04\x00\x00\x00abcd" \
        + b"data" + struct.pack("<I", len(body)) + body
    fish(wav, block=3)
    assert b"".join(_collect()) == body


def test_error_before_sound_raises_and_speak_pcm_returns_none(fish):
    fish(b"", exc=OSError("нет сети"))
    with pytest.raises(OSError):
        _collect()
    assert asyncio.run(tts_fish.speak_pcm("Привет, сэр.")) is None


def test_tightener_streaming_equals_whole():
    rng = np.random.default_rng(3)
    r = 24000
    t = np.arange(int(r * 0.5)) / r
    tone = (7000 * np.sin(2 * np.pi * 200 * t)).astype(np.int16)
    q = lambda s: rng.normal(0, 15, int(r * s)).astype(np.int16)   # noqa: E731
    clip = np.concatenate([q(0.3), tone, q(0.05), tone, q(0.8), tone, q(0.5)]).tobytes()
    whole = P.tighten(clip, r)
    tt = P.Tightener(r)
    out, i = b"", 0
    while i < len(clip):
        n = int(rng.integers(1, 3000)) * 2 - 1                      # и нечётные куски
        out += tt.feed(clip[i:i + n])
        i += n
    out += tt.finish(True)
    assert out == whole and tt.dropped > 0


class _Stub:
    _fish_worker = jarvis_main.Jarvis._fish_worker

    def __init__(self):
        self._speaking_lock = threading.Lock()
        self._active_synth_tasks = 0
        self.audio_in_queue = asyncio.Queue()
        self.ui = SimpleNamespace(write_log=lambda s: None)
        self._latency = LatencyTracker(enabled=False)

    def set_speaking(self, v):
        pass


def test_sound_plays_before_synthesis_finishes(monkeypatch, tmp_path):
    """Первый кусок звука уже в динамиках, а Fish ещё синтезирует хвост."""
    from core import quick
    monkeypatch.setattr(quick, "_cache", quick.VoiceCache(tmp_path))
    monkeypatch.setattr(tts_fish, "is_configured", lambda: True)
    loud = (np.full(2400, 6000, dtype=np.int16)).tobytes()            # 0,1 с звука
    gate = asyncio.Event()
    seen = {}

    async def stream_pcm(text, sample_rate=24000):
        yield loud
        await gate.wait()                                             # «хвост ещё синтезируется»
        yield loud

    monkeypatch.setattr(tts_fish, "stream_pcm", stream_pcm)

    async def go():
        stub = _Stub()
        q = asyncio.Queue()
        q.put_nowait("Проверка потока для длинного ответа, сэр.")
        q.put_nowait(None)
        task = asyncio.create_task(stub._fish_worker(q))
        for _ in range(100):
            if not stub.audio_in_queue.empty():
                break
            await asyncio.sleep(0.01)
        seen["early"] = stub.audio_in_queue.qsize()
        gate.set()
        await task
        seen["total"] = sum(len(stub.audio_in_queue.get_nowait()) for _ in range(stub.audio_in_queue.qsize()))
    asyncio.run(go())
    assert seen["early"] > 0, "звук ждал конца синтеза"
    assert seen["total"] >= 2 * len(loud)


# ── соединения: переиспользуются, мёртвый сокет из пула — один повтор ──────────
class _FakeHTTPResp:
    def __init__(self, data, status=200):
        self._d, self.status, self.reason, self.headers, self.will_close = data, status, "OK", {}, False

    def read(self, n=-1):
        out, self._d = (self._d, b"") if n is None or n < 0 else (self._d[:n], self._d[n:])
        return out

    def read1(self, n=-1):
        return self.read(n)

    def isclosed(self):
        return not self._d


class _FakeConn:
    made = 0

    def __init__(self, dead=False):
        _FakeConn.made += 1
        self.dead, self.closed, self.requests = dead, False, 0

    def request(self, *a, **k):
        if self.dead:
            raise ConnectionResetError("сервер закрыл простой сокет")
        self.requests += 1

    def getresponse(self):
        return _FakeHTTPResp(b"\x01\x02" * 10)

    def close(self):
        self.closed = True


def test_connection_is_reused_and_dead_pooled_socket_is_retried(monkeypatch):
    for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(tts_fish, "_key", lambda: "k")
    monkeypatch.setattr(tts_fish, "_pool", [])
    _FakeConn.made = 0
    monkeypatch.setattr(tts_fish, "_new_conn", lambda: _FakeConn())
    with tts_fish._open("раз", "pcm", "low", 24000) as r:
        assert r.read() == b"\x01\x02" * 10
    with tts_fish._open("два", "pcm", "low", 24000) as r:
        r.read()
    assert _FakeConn.made == 1                              # второй раз — то же соединение
    import time
    tts_fish._pool[:] = [(time.monotonic(), _FakeConn(dead=True))]
    with tts_fish._open("три", "pcm", "low", 24000) as r:
        assert r.read()                                     # повтор на свежем — без ошибки
