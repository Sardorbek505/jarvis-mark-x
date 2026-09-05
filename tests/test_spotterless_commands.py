"""Тесты для быстрых команд без имени (Spotterless Quick Commands, как у Алисы)."""

import json
from unittest.mock import MagicMock
import pytest
from core.wake_detector import WakeWordDetector2Stage


class DummyVoskRec:
    def __init__(self, result_text: str = "", partial_text: str = "", accept: bool = True):
        self._result_text = result_text
        self._partial_text = partial_text
        self._accept = accept

    def AcceptWaveform(self, pcm):
        return self._accept

    def Result(self):
        return json.dumps({"text": self._result_text})

    def PartialResult(self):
        return json.dumps({"partial": self._partial_text})

    def Reset(self):
        pass


def test_spotterless_pause():
    wake_calls = []
    quick_calls = []

    detector = WakeWordDetector2Stage(
        on_wake=lambda score: wake_calls.append(score),
        on_quick_command=lambda cmd: quick_calls.append(cmd),
    )
    detector._vosk_rec = DummyVoskRec(result_text="пауза", accept=True)

    dummy_pcm = b"\x00\x01" * 1280
    result = detector.process_pcm(dummy_pcm)

    # Не должно триггерить полное пробуждение в облако
    assert result is False
    assert wake_calls == []
    # Быстрая команда должна быть перехвачена
    assert quick_calls == ["пауза"]


def test_spotterless_volume_down():
    quick_calls = []

    detector = WakeWordDetector2Stage(
        on_quick_command=lambda cmd: quick_calls.append(cmd),
    )
    detector._vosk_rec = DummyVoskRec(result_text="сделай тише", accept=True)

    dummy_pcm = b"\x00\x01" * 1280
    result = detector.process_pcm(dummy_pcm)

    assert result is False
    assert quick_calls == ["сделай тише"]


def test_spotterless_partial_instant_stop():
    quick_calls = []

    detector = WakeWordDetector2Stage(
        on_quick_command=lambda cmd: quick_calls.append(cmd),
    )
    # На лету (в PartialResult) сказано 'стоп'
    detector._vosk_rec = DummyVoskRec(partial_text="стоп", accept=False)

    dummy_pcm = b"\x00\x01" * 1280
    result = detector.process_pcm(dummy_pcm)

    assert result is False
    assert quick_calls == ["стоп"]


def test_spotterless_long_sentence_filtered():
    quick_calls = []

    detector = WakeWordDetector2Stage(
        on_quick_command=lambda cmd: quick_calls.append(cmd),
    )
    # Длинная посторонняя фраза, в которой есть слово 'пауза'
    detector._vosk_rec = DummyVoskRec(result_text="я вчера нажал на паузу в игре", accept=True)

    dummy_pcm = b"\x00\x01" * 1280
    result = detector.process_pcm(dummy_pcm)

    assert result is False
    assert quick_calls == []  # Отфильтровано, 0 ложных срабатываний


def test_spotterless_wake_word_still_works():
    wake_calls = []
    quick_calls = []

    detector = WakeWordDetector2Stage(
        on_wake=lambda score: wake_calls.append(score),
        on_quick_command=lambda cmd: quick_calls.append(cmd),
    )
    # Обращение 'джарвис'
    detector._vosk_rec = DummyVoskRec(result_text="джарвис", accept=True)

    dummy_pcm = b"\x00\x01" * 1280
    result = detector.process_pcm(dummy_pcm)

    assert result is True
    assert len(wake_calls) == 1
    assert quick_calls == []
