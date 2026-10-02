"""Слух: наушники не «слышат» колонки, подсказки распознаванию, порог эха.

В игре в наушниках Джарвис был глух: гарнитуру с именем «Микрофон (USB Audio
Device)» считали слышащей колонки и глушили по громкости игры."""
import importlib


def test_headphones_output_means_mic_does_not_hear_speakers(monkeypatch):
    import main as m
    monkeypatch.setenv("JARVIS_OUTPUT_DEVICE", "Наушники (Realtek High Definition Audio)")
    assert m._output_is_headphones()
    assert m._mic_hears_speakers(None) is False
    monkeypatch.setenv("JARVIS_OUTPUT_DEVICE", "Динамики (Realtek High Definition Audio)")
    assert not m._output_is_headphones()
    monkeypatch.setenv("JARVIS_OUTPUT_DEVICE", "JBL Flip 5 Bluetooth")   # колонка, не наушники
    assert not m._output_is_headphones()


def test_asr_hints_and_fallback():
    import main as m
    j = m.Jarvis.__new__(m.Jarvis)
    j._asr_hints = True
    cfg = j._asr_config()
    assert "ru-RU" in cfg["language_codes"] and "Джарвис" in cfg["custom_vocabulary"]
    j._asr_hints = False                                   # сессия не приняла — без подсказок
    assert j._asr_config() == {}


def test_echo_threshold_is_configurable(monkeypatch):
    import core.echo_gate as eg
    monkeypatch.setenv("MIC_ECHO_K", "2.5")
    assert importlib.reload(eg).EchoGate.K == 2.5
    monkeypatch.delenv("MIC_ECHO_K")
    assert importlib.reload(eg).EchoGate.K == 2.0
