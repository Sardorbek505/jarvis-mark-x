"""Секундомер голосового хода не должен врать и не должен ронять ассистента."""

import sys
from pathlib import Path

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
