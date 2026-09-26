"""Измеритель динамиков: COM-объект живёт только на своём потоке.

Живой отказ: start() создавал измеритель на вызывающем потоке просто чтобы
проверить, что это возможно, и выбрасывал его. Настоящий создавался заново в
рабочем потоке. Выброшенный COM-указатель потом освобождался сборщиком мусора
на потоке без CoInitialize:

    Exception ignored in: <function _compointer_base.__del__>
    OSError: exception: access violation writing 0x0000000000000000
"""
import threading

import pytest

from speaker_meter import SpeakerMeter


class FakeMeter:
    def GetPeakValue(self):
        return 0.25


@pytest.fixture
def meter(monkeypatch):
    m = SpeakerMeter(poll_sec=0.01)
    monkeypatch.setattr(SpeakerMeter, "_default_id", staticmethod(lambda: "dev-1"))
    return m


def test_meter_is_never_created_on_the_calling_thread(meter, monkeypatch):
    threads = []

    def spy():
        threads.append(threading.current_thread().name)
        return FakeMeter()

    monkeypatch.setattr(SpeakerMeter, "_make_meter", staticmethod(spy))
    caller = threading.current_thread().name

    assert meter.start() is True
    meter.stop()

    assert threads, "измеритель вообще не создавался"
    assert caller not in threads, f"COM-объект создан на чужом потоке: {threads}"


def test_start_reports_failure_when_meter_cannot_be_created(meter, monkeypatch):
    def boom():
        raise OSError("нет устройства вывода")

    monkeypatch.setattr(SpeakerMeter, "_make_meter", staticmethod(boom))

    assert meter.start() is False
    assert meter.available is False


def test_peak_is_read_after_successful_start(meter, monkeypatch):
    monkeypatch.setattr(SpeakerMeter, "_make_meter", staticmethod(lambda: FakeMeter()))

    assert meter.start() is True
    deadline = threading.Event()
    deadline.wait(0.2)
    meter.stop()

    assert meter.peak == pytest.approx(0.25)
