"""Пропажа звукового устройства не должна делать Джарвиса немым навсегда.

06.08 в живом запуске: `stream.write` упал с MME error 6 («There is no
driver installed on your system») — устройство на секунду стало недоступно.
Цикл воспроизведения сидел ВНУТРИ try, finally закрывал поток, и задача
завершалась. Пересоздавать её было некому: _play_audio создаётся единожды
в TaskGroup, а комментарий рядом обещал «let the task be recreated».

Снаружи это выглядело как «слышит, но не говорит»: распознавание работало,
ответы генерировались, звука не было до перезапуска.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as jarvis_main
from core.latency import LatencyTracker


class _Stream:
    """Поток вывода, который падает на N-й записи."""

    def __init__(self, fail_at=None):
        self.written = []
        self._n = 0
        self._fail_at = fail_at
        self.closed = False

    def write(self, chunk):
        self._n += 1
        if self._fail_at and self._n == self._fail_at:
            raise RuntimeError("Unanticipated host error [MME error 6]")
        self.written.append(chunk)

    def stop(self):
        pass

    def close(self):
        self.closed = True


class _Stub:
    """Минимальный носитель состояния для _play_audio."""

    def __init__(self, streams):
        self._streams = list(streams)
        self.opened = []
        self.audio_in_queue = asyncio.Queue()
        self._turn_done_event = asyncio.Event()
        self._speaking_lock = None
        self._active_synth_tasks = 0
        self._is_speaking = False
        self.ui = SimpleNamespace(write_log=lambda *a: None)
        self.speaking = []
        # Громкость кадра, которой дышит HUD. Копим, чтобы проверить: волна
        # должна брать амплитуду настоящего звука, а не рисовать своё.
        self.levels: list[float] = []
        # _play_audio отмечает первый кадр в устройстве; здесь замер не нужен
        self._latency = LatencyTracker(enabled=False)

    def _push_level(self, value):
        self.levels.append(value)

    def _open_output(self):
        s = self._streams.pop(0)
        self.opened.append(s)
        return s

    def set_speaking(self, v):
        self.speaking.append(v)


async def _run_playback(stub, chunks, seconds=0.4, retry=0.02):
    # Пауза перед переоткрытием — секунда в бою, здесь незачем её ждать.
    jarvis_main._PLAYBACK_RETRY_SEC = retry
    play = jarvis_main.Jarvis._play_audio.__get__(stub, jarvis_main.Jarvis)
    task = asyncio.create_task(play())
    for c in chunks:
        await stub.audio_in_queue.put(c)
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return task


@pytest.mark.asyncio
async def test_дефект_кода_не_выдаётся_за_поломку_устройства():
    """AttributeError в своём же коде раньше уходил в ветку «звук отвалился»:
    пользователь слышал про устройство, а цикл вечно переоткрывал исправную
    карту. Такая ошибка обязана вылететь наверх, где есть счётчик попыток."""
    class _Broken(_Stub):
        def _open_output(self):
            raise AttributeError("'_Broken' object has no attribute '_latency'")

    stub = _Broken([])
    stub.audio_in_queue.put_nowait(b"\x01\x02")

    with pytest.raises(AttributeError):
        await asyncio.wait_for(
            jarvis_main.Jarvis._play_audio(stub), timeout=2,
        )


@pytest.mark.asyncio
async def test_волна_на_hud_следует_за_настоящим_звуком():
    """HUD обязан дышать амплитудой того, что реально звучит.

    Раньше он «реагировал» на random.uniform: одинаково в тишине и на крике,
    то есть был заставкой, а не индикатором. Проверяем на двух кадрах —
    тишине и громком — что уровень берётся из самих сэмплов.
    """
    import numpy as np

    тишина = np.zeros(1024, dtype=np.int16).tobytes()
    громкий = (np.ones(1024, dtype=np.int16) * 9000).tobytes()

    stub = _Stub([_Stream()])
    await _run_playback(stub, [тишина, громкий])

    assert len(stub.levels) == 2, "уровень должен уходить на каждый кадр"
    assert stub.levels[0] == 0.0, "тишина обязана быть нулём, а не случайностью"
    assert stub.levels[1] > 0.9, "громкий кадр обязан поднять волну"


@pytest.mark.asyncio
async def test_звук_доходит_до_устройства():
    stub = _Stub([_Stream()])
    await _run_playback(stub, [b"\x01\x02", b"\x03\x04"])
    assert stub.opened[0].written == [b"\x01\x02", b"\x03\x04"]


@pytest.mark.asyncio
async def test_после_сбоя_устройство_переоткрывается():
    """Главное: задача не завершается, а берёт новый поток."""
    broken, good = _Stream(fail_at=1), _Stream()
    stub = _Stub([broken, good])
    await _run_playback(stub, [b"\x01\x02", b"\x03\x04"])

    assert len(stub.opened) >= 2, "поток должен быть открыт заново"
    assert broken.closed, "сломанный поток закрыт"
    assert good.written, "после переоткрытия звук снова идёт"


@pytest.mark.asyncio
async def test_сбой_не_завершает_задачу():
    stub = _Stub([_Stream(fail_at=1), _Stream(), _Stream()])
    task = await _run_playback(stub, [b"\x01\x02"])
    assert task.cancelled(), "задача жила до самой отмены, а не умерла сама"


@pytest.mark.asyncio
async def test_отмена_не_считается_сбоем():
    """CancelledError должен пробрасываться, а не уходить в переоткрытие."""
    stub = _Stub([_Stream() for _ in range(3)])
    await _run_playback(stub, [], seconds=0.15)
    assert len(stub.opened) == 1, "устройство не должно переоткрываться на пустой очереди"
