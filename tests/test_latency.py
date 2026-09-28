"""Секундомер голосового хода не должен врать и не должен ронять ассистента."""

import re
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core.latency import LatencyTracker  # noqa: E402


def _tracker(**kw):
    return LatencyTracker(enabled=True, **kw)


def test_замер_считается_от_последнего_кадра_а_не_от_первого(monkeypatch):
    """Человек говорил 3 секунды — это не задержка ответа."""
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    lines = []
    t = _tracker(sink=lines.append)

    # Arrange — речь длиной 3 с, кадры идут вплотную
    for tick in (0.0, 0.2, 0.4, 3.0):
        clock[0] = tick
        t.mark_voice_frame()

    # Act — ответ пришёл через 0.5 с после последнего кадра
    clock[0] = 3.5
    t.mark_answer_audio()
    t.mark_turn_complete()

    # Assert — 500 мс, а не 3500
    assert "отвечает 500мс" in lines[0]


def test_свой_голос_в_микрофоне_не_даёт_отрицательных_миллисекунд(monkeypatch):
    """Джарвис говорит, микрофон слышит его же — точка отсчёта уже замерла.

    Живой журнал 28.09 дал «отвечает -3937мс · звучит -5550мс»: пока звучал
    ответ на 4.6 с, громкие кадры продолжали идти и уезжали за момент ответа.
    """
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    lines = []
    t = _tracker(sink=lines.append)

    # Arrange — человек договорил на 1.0
    for tick in (0.0, 0.5, 1.0):
        clock[0] = tick
        t.mark_voice_frame()

    # Act — ответ через 0.4 с, а потом 4 с своего голоса обратно в микрофон
    clock[0] = 1.4
    t.mark_answer_audio()
    clock[0] = 1.5
    t.mark_playback()
    tick = 1.6
    while tick < 5.5:                 # кадры идут вплотную, паузы нет
        clock[0] = tick
        t.mark_voice_frame()
        tick += 0.1
    t.mark_turn_complete()

    # Assert — считаем от конца речи человека, а не от своего эха
    assert "звучит 500мс" in lines[0]
    assert [int(v) for v in re.findall(r"(-?\d+)мс", lines[0])] == pytest.approx([400, 500], abs=1)


def test_пауза_начинает_новый_ход(monkeypatch):
    """Хвост прошлой реплики не должен приписаться к следующему вопросу."""
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    lines = []
    t = _tracker(sink=lines.append)

    clock[0] = 0.0
    t.mark_voice_frame()
    clock[0] = 10.0          # пауза много больше порога — это уже новая реплика
    t.mark_voice_frame()
    clock[0] = 10.3
    t.mark_answer_audio()
    t.mark_turn_complete()

    assert "отвечает 300мс" in lines[0]


def test_первый_отклик_а_не_каждый_кадр(monkeypatch):
    """В статистику идёт первый байт ответа, последующие его не сдвигают."""
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    lines = []
    t = _tracker(sink=lines.append)

    t.mark_voice_frame()
    clock[0] = 0.4
    t.mark_answer_audio()
    clock[0] = 2.0
    t.mark_answer_audio()    # ещё кадры того же ответа
    t.mark_turn_complete()

    assert "отвечает 400мс" in lines[0]


def test_ход_без_ответа_не_попадает_в_статистику(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    lines = []
    t = _tracker(sink=lines.append)

    t.mark_voice_frame()
    t.mark_turn_complete()   # модель промолчала

    assert lines == []
    assert "Замеров не набралось" in t.summary()


def test_время_инструмента_видно_в_строке(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    lines = []
    t = _tracker(sink=lines.append)

    t.mark_voice_frame()
    t.add_tool("weather", 1200)
    clock[0] = 1.5
    t.mark_answer_audio()
    t.mark_turn_complete()

    assert "weather 1200мс" in lines[0]


def test_сводка_считает_медиану_и_худшую(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    t = _tracker()

    for delay in (0.1, 0.2, 0.9):
        base = clock[0]
        t.mark_voice_frame()
        clock[0] = base + delay
        t.mark_answer_audio()
        t.mark_turn_complete()
        clock[0] += 5.0      # разрыв между ходами

    summary = t.summary()
    assert "3 ходов" in summary
    assert "медиана 200мс" in summary
    assert "худшая 900мс" in summary


def test_выключенный_замерщик_молчит():
    lines = []
    t = LatencyTracker(enabled=False, sink=lines.append)

    t.mark_voice_frame()
    t.mark_answer_audio()
    t.mark_turn_complete()

    assert lines == []
    assert t.summary() == ""


def test_проактивная_реплика_не_мерится_от_древнего_кадра(monkeypatch):
    """Джарвис заговорил сам — это не «ответ за 60 секунд»."""
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    lines = []
    t = _tracker(sink=lines.append)

    # Arrange — обычный ход завершился
    t.mark_voice_frame()
    clock[0] = 0.3
    t.mark_answer_audio()
    t.mark_turn_complete()
    lines.clear()

    # Act — через минуту тишины ассистент заговорил по своей инициативе
    clock[0] = 60.0
    t.mark_answer_audio()
    t.mark_turn_complete()

    # Assert — в статистику это не попало
    assert lines == []
    assert t._stats["answered"].count == 1


def test_сбой_приёмника_не_ломает_ход():
    """Замер не имеет права уронить ассистента — даже если сломан вывод."""
    def bad_sink(_):
        raise RuntimeError("UI отвалился")

    t = _tracker(sink=bad_sink)
    t.mark_voice_frame()
    t.mark_answer_audio()
    t.mark_turn_complete()   # не должно бросить наружу


def test_этапы_хода_видны_по_порядку(monkeypatch):
    """Где ушли секунды: Gemini заговорил, пришёл текст, ушёл в озвучку, зазвучал Fish."""
    clock = [0.0]
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: clock[0])
    lines = []
    t = _tracker(sink=lines.append)
    t.mark_voice_frame()
    for tick, what in ((0.6, "heard"), (1.4, "gemini-звук"), (1.9, "текст"), (3.1, "в озвучку"),
                       (3.9, "answered"), (4.0, "gemini-звук")):
        clock[0] = tick
        if what == "heard":
            t.mark_transcript()
        elif what == "answered":
            t.mark_answer_audio()
        else:
            t.mark(what)                                    # повтор этапа не перезаписывает первый
    t.mark_turn_complete()
    assert lines[0] == ("SYS: ⏱  слышит 600мс · gemini-звук 1400мс · текст 1900мс · "
                        "в озвучку 3100мс · отвечает 3900мс")
    clock[0] = 10.0
    t.mark_voice_frame()
    t.mark_answer_audio()
    t.mark_turn_complete()
    assert "gemini-звук" not in lines[1]                    # этапы — только своего хода
