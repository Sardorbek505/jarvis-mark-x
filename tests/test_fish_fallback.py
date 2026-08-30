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

    async def fake_edge(fragment, sample_rate=24000):
        edge_calls.append(fragment)
        return b"\x00\x01" * 600

    monkeypatch.setattr(tts_fish, "is_configured", lambda: configured)
    monkeypatch.setattr(tts_fish, "speak_pcm", fake_fish)
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
