"""Проводка собранного рантайма: Jarvis + AudioPipeline + ConversationStateMachine.

Зачем отдельный файл. Модули голосового тракта покрыты тестами поштучно, и все
они зелёные даже когда собранный ассистент не работает: конвейер тестируется с
подставной машиной состояний, машина состояний — без конвейера, а `main.Jarvis`
не тестировался вовсе. Ровно так и вышло, что «функции есть, а реализации нет».

Здесь проверяется то, что видно только в сборке:
  * конвейер действительно создан, запущен и подключён к машине состояний;
  * источник опорного сигнала для AEC заведён (без него эхоподавления нет);
  * интерфейс следует за состояниями;
  * кадр микрофона проходит весь путь callback -> очередь -> воркер -> out_queue,
    и только при открытом шлюзе.
"""

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core.conversation_state import ConversationState
from core.headless_ui import HeadlessUI
from main import Jarvis, CHUNK_SIZE


@pytest.fixture
def jarvis():
    """Собранный Jarvis без окна, телеграм-процесса, хоткеев и тяжёлых моделей KWS."""
    with patch("core.hotkey_manager.GlobalHotkeyManager"), \
         patch("core.audio_pipeline.WakeWordDetector2Stage") as wake_cls, \
         patch("main.Jarvis._start_telegram_bot", return_value=None):
        wake_cls.return_value.process_pcm = MagicMock(return_value=False)
        j = Jarvis(HeadlessUI())
        yield j
        j.cleanup()


def _loud_frame(value=4000, seed=0):
    """Кадр, похожий на речь: постоянный уровень нейросетевой VAD речью не считает."""
    rng = np.random.default_rng(seed)
    t = np.arange(CHUNK_SIZE) / 16000
    sig = np.zeros(CHUNK_SIZE)
    for f, a in ((120, 0.5), (350, 0.35), (900, 0.2), (2400, 0.1)):
        sig += a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4.5 * t)
    return np.clip(sig * env * 9000, -32768, 32767).astype(np.int16).tobytes()


def test_audio_pipeline_is_wired_and_running(jarvis):
    """Конвейер создан, воркер жив, детектор — тот же объект, что у конвейера."""
    pipe = jarvis.audio_pipeline
    assert pipe is not None, "AudioPipeline не поднялся — Джарвис остался без слуха"
    assert pipe._running is True
    assert pipe._worker_thread is not None and pipe._worker_thread.is_alive()
    assert jarvis._wake_detector is pipe.wake_detector, "два разных детектора вместо одного"


def test_aec_reference_source_is_wired(jarvis):
    """У конвейера есть источник опорного сигнала колонок.

    Регрессия: `push_frame` вызывался без `ref_pcm`, поэтому AECPipeline не
    выполнялся ни разу, хотя в лог писалось, что AEC подключён.
    """
    pipe = jarvis.audio_pipeline
    assert pipe.ref_provider == jarvis._aec_reference_window
    assert pipe.enable_aec is True
    # Пока loopback не поднят, провайдер обязан отдавать пустоту, а не падать.
    assert jarvis._aec_reference_window(0.0, 640) == b""
    # И конвейер обязан честно говорить, что эхоподавление ещё не работает.
    assert pipe.aec_active is False


def test_gateway_provider_reflects_wake_window(jarvis):
    """Шлюз открыт ровно тогда, когда открыто окно wake-слова или хоткея."""
    import time
    pipe = jarvis.audio_pipeline
    jarvis._wake_active_until = 0.0
    jarvis._hotkey_active_until = 0.0
    assert pipe._gateway_open() is False

    jarvis._wake_active_until = time.monotonic() + 5.0
    assert pipe._gateway_open() is True


def test_ui_follows_state_machine(jarvis):
    """Интерфейс подписан на машину состояний, а не выставляется вручную по местам."""
    sm = jarvis.state_machine
    assert sm.state == ConversationState.STANDBY

    sm.transition_to(ConversationState.LISTENING, reason="test")
    assert jarvis.ui.state == "LISTENING"

    sm.transition_to(ConversationState.SPEAKING, reason="test")
    assert jarvis.ui.state == "SPEAKING"

    sm.transition_to(ConversationState.STANDBY, reason="test")
    assert jarvis.ui.state == "IDLE"


@pytest.mark.asyncio
async def test_frame_reaches_cloud_only_through_open_gate(jarvis):
    """Полный путь кадра: push_frame -> очередь -> воркер -> out_queue.

    И главная гарантия приватности: при закрытом шлюзе в облако не уходит ничего.
    """
    import time
    jarvis.out_queue = asyncio.Queue(maxsize=50)
    jarvis._loop = asyncio.get_running_loop()
    jarvis._wake_active_until = 0.0
    jarvis._hotkey_active_until = 0.0
    jarvis._chime_until = 0.0

    # 12 кадров ≈ 0.8 с — реплика, а не щелчок: нейросетевому VAD нужно
    # несколько кадров, чтобы раскачать рекуррентное состояние.
    for i in range(12):
        jarvis.audio_pipeline.push_frame(_loud_frame(seed=i))
    for _ in range(20):
        await asyncio.sleep(0.02)
    assert jarvis.out_queue.empty(), "шлюз закрыт, а звук уехал в облако"
    assert jarvis.state_machine.state == ConversationState.STANDBY

    # Открываем окно активности — так же, как это делает wake word или F8.
    jarvis._wake_active_until = time.monotonic() + 5.0
    for i in range(12):
        jarvis.audio_pipeline.push_frame(_loud_frame(seed=i))

    deadline = asyncio.get_running_loop().time() + 3.0
    while jarvis.out_queue.empty() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)

    assert not jarvis.out_queue.empty(), "шлюз открыт, а речь до облака не дошла"
    item = jarvis.out_queue.get_nowait()
    assert item["mime_type"] == "audio/pcm"
    assert jarvis.state_machine.state == ConversationState.LISTENING


def test_quick_command_does_not_block_the_audio_worker(jarvis):
    """Медленная быстрая команда не морозит разбор звука.

    Роутер выглядит мгновенным только на медиа-клавишах. «Полный экран»,
    «перемотай», «включи фильм» уходят в `actions/movie_player.py` с паузами до
    2 секунд, а «посмотри на экран» — это снимок экрана и запрос к модели.
    Пока всё это крутилось прямо в аудиоворкере, ограниченная очередь (100
    кадров ≈ 3 с) переполнялась: звук терялся, ключевое слово и перебивание
    не работали.
    """
    import time

    def _slow_router(*args, **kwargs):
        time.sleep(1.0)
        return (True, "")

    with patch("core.fast_command_router.FastCommandRouter.match_and_execute", _slow_router), \
         patch("core.earcons.play_success_earcon"):
        started = time.perf_counter()
        jarvis._handle_quick_command("полный экран")
        elapsed = time.perf_counter() - started

        assert elapsed < 0.3, (
            f"вызывающий поток заблокирован на {elapsed:.2f} с — конвейер в это время глухой"
        )
        assert jarvis._fast_command_thread is not None
        assert jarvis._fast_command_thread.is_alive(), "команда должна выполняться в фоне"
        jarvis._fast_command_thread.join(timeout=5.0)


@pytest.mark.asyncio
async def test_quick_command_does_not_leak_speech_to_cloud(jarvis):
    """Быстрая команда исполняется локально и чистит очередь исходящего аудио.

    Иначе та же фраза уезжает в Gemini и выполняется вторым заходом — двойной
    «следующий трек» на одну реплику.
    """
    jarvis.out_queue = asyncio.Queue(maxsize=50)
    jarvis._loop = asyncio.get_running_loop()
    jarvis.out_queue.put_nowait({"data": b"\x00\x00", "mime_type": "audio/pcm"})

    with patch("core.fast_command_router.FastCommandRouter.match_and_execute") as router, \
         patch("core.earcons.play_success_earcon"):
        router.return_value = (True, "")
        jarvis._handle_quick_command("пауза")
        if jarvis._fast_command_thread:
            jarvis._fast_command_thread.join(timeout=5.0)

    await asyncio.sleep(0.05)
    assert jarvis.out_queue.empty(), "речь быстрой команды осталась в очереди на облако"
    assert jarvis._wake_active_until == 0.0, "после локального действия шлюз обязан закрыться"
