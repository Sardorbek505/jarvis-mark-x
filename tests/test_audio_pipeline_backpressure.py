"""Тесты ограниченной очереди (bounded queue) и backpressure в AudioPipeline."""

import time
import numpy as np
from core.audio_pipeline import AudioPipeline
from core.conversation_state import ConversationState, ConversationStateMachine


def test_audio_pipeline_non_blocking_push_under_overflow():
    """Тест: push_frame никогда не блокирует вызывающий поток при переполнении очереди."""
    max_q = 50
    pipeline = AudioPipeline(max_queue_size=max_q)
    # Не запускаем воркер-поток, чтобы очередь гарантированно заполнилась
    pipeline._running = True

    dummy_mic = b"\x00\x00" * 512
    dummy_ref = b"\x00\x00" * 512

    start_time = time.perf_counter()
    num_frames = 200

    for _ in range(num_frames):
        pipeline.push_frame(dummy_mic, dummy_ref)

    elapsed = time.perf_counter() - start_time

    # 200 вызовов push_frame должны выполняться за доли секунды (< 0.1 с)
    assert elapsed < 0.2, f"push_frame заблокировался: заняло {elapsed:.3f} с"

    stats = pipeline.get_stats()
    assert stats["queue_size"] <= max_q
    assert stats["drop_count"] >= (num_frames - max_q)
    assert pipeline.drop_count == stats["drop_count"]

    pipeline.stop()


def test_audio_pipeline_worker_draining():
    """Тест: воркер корректно вычитывает кадры из очереди."""
    received_frames = []
    sm = ConversationStateMachine(initial_state=ConversationState.LISTENING)

    pipeline = AudioPipeline(
        state_machine=sm,
        on_speech_frame=lambda frame: received_frames.append(frame),
        max_queue_size=20,
        enable_aec=False,
        # Проверяется прокачка очереди воркером, а не распознавание речи:
        # случайный шум Silero справедливо речью не считает.
        enable_endpointing=False,
    )
    pipeline.start()

    dummy_mic = (np.random.randint(-100, 100, 512, dtype=np.int16)).tobytes()
    for _ in range(10):
        pipeline.push_frame(dummy_mic)

    # Даем воркеру время на обработку
    timeout = time.time() + 2.0
    while len(received_frames) < 10 and time.time() < timeout:
        time.sleep(0.05)

    pipeline.stop()
    assert len(received_frames) >= 10
    assert pipeline.drop_count == 0
