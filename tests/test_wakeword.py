"""Тесты для детектора Wake-Word и генератора аудио-отклика."""
from core import wakeword


def test_chime_generation():
    pcm = wakeword.generate_chime_pcm(sample_rate=24000)
    assert isinstance(pcm, bytes)
    assert len(pcm) > 5000  # Должно быть ~8640 байт


def test_wakeword_keyword_matching():
    det = wakeword.WakeWordDetector()
    assert det.is_keyword_in_text("Джарвис, включи музыку") is True
    assert det.is_keyword_in_text("JARVIS what time is it") is True
    assert det.is_keyword_in_text("Слушай, напомни завтра") is True
    assert det.is_keyword_in_text("Какая сегодня температура на улице") is False


def test_trigger_wake_callback():
    called = []
    det = wakeword.WakeWordDetector(on_wake=lambda: called.append(True))
    det.trigger_wake()
    assert len(called) == 1
