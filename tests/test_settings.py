"""Настройки Джарвиса: файл, окружение, применение на лету (микрофон/динамик
переоткрываются, порог и обращение меняются без перезапуска), устройства по
имени и экран «Настройки» (всё сохраняется сразу)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import settings as S  # noqa: E402


@pytest.fixture(autouse=True)
def _env_back():
    """S.set пишет прямо в os.environ — после теста возвращаем как было."""
    keys = [o.env for o in S.OPTS if o.env]
    before = {k: os.environ.get(k) for k in keys}
    yield
    S._subs.clear()
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_defaults_env_and_saved(monkeypatch):
    monkeypatch.delenv("MIC_RMS_THRESHOLD", raising=False)
    monkeypatch.setenv("JARVIS_AWAKE_SEC", "60")                   # задано в .env и не тронуто
    v = S.load()
    assert v["mic"] == "" and v["mic_threshold"] == 150 and v["awake_sec"] == 60 and v["briefing"] is False
    got = []
    S.subscribe(lambda k, val: got.append((k, val)))
    try:
        S.set("mic_threshold", "300")
        S.set("ignore_speakers", 0)
        S.set("mic", "Гарнитура (AirPods Pro)")
    finally:
        S._subs.clear()
    assert got == [("mic_threshold", 300), ("ignore_speakers", False), ("mic", "Гарнитура (AirPods Pro)")]
    assert os.environ["MIC_RMS_THRESHOLD"] == "300" and os.environ["MIC_IGNORE_SPEAKERS"] == "0"
    assert os.environ["MIC_DEVICE"] == "Гарнитура (AirPods Pro)"
    # новый запуск: окружение пустое — берётся из файла
    for k in ("MIC_RMS_THRESHOLD", "MIC_IGNORE_SPEAKERS", "MIC_DEVICE"):
        os.environ.pop(k)
    S.apply_env()
    assert os.environ["MIC_RMS_THRESHOLD"] == "300" and os.environ["MIC_DEVICE"].startswith("Гарнитура")
    assert "JARVIS_AWAKE_SEC" in os.environ and os.environ["JARVIS_AWAKE_SEC"] == "60"   # не тронуто — не трогаем
    S.set("mic", "")                                                # снова автовыбор
    assert "MIC_DEVICE" not in os.environ and S.get("mic") == ""


def test_voice_goes_to_keys_where_main_reads_it(monkeypatch):
    saved = {}
    monkeypatch.setattr("core.paths.save_api_keys", lambda d: saved.update(d) or True)
    S.set("voice", "gemini")
    assert saved == {"jarvis_voice": "gemini"} and S.get("voice") == "gemini"


def test_find_device_by_name():
    devs = [{"name": "Микрофон (Realtek(R) Audio)", "max_input_channels": 2, "max_output_channels": 0},
            {"name": "Динамики (Realtek(R) Audio)", "max_input_channels": 0, "max_output_channels": 2},
            {"name": "Наушники (AirPods Pro Stereo", "max_input_channels": 0, "max_output_channels": 2}]
    q = lambda: devs  # noqa: E731
    assert S.find_device("Динамики (Realtek(R) Audio)", "output", q) == 1
    assert S.find_device("Динамики (Realtek(R) Audio)", "input", q) is None     # не тот вид
    assert S.find_device("Наушники (AirPods Pro Stereo Hands-Free)", "output", q) == 2   # MME режет имя
    assert S.find_device("", "output", q) is None and S.find_device("Колонка JBL", "output", q) is None


def test_main_applies_settings_live():
    import main
    fake = type("J", (), {"_mic_reopen": False, "_out_reopen": False})()
    on = main.Jarvis._on_setting
    old = (main.MIC_RMS_THRESHOLD, main._WAKE_MODE, main._AWAKE_SEC, main._IGNORE_SPEAKERS)
    try:
        on(fake, "mic", "Гарнитура")
        on(fake, "speaker", "Наушники")
        assert fake._mic_reopen and fake._out_reopen                 # переоткроются между фразами
        on(fake, "mic_threshold", 320)
        on(fake, "wake_mode", "always_on")
        on(fake, "awake_sec", 120)
        on(fake, "ignore_speakers", False)
        assert (main.MIC_RMS_THRESHOLD, main._WAKE_MODE, main._AWAKE_SEC, main._IGNORE_SPEAKERS) == \
            (320.0, "always_on", 120.0, False)
    finally:
        main.MIC_RMS_THRESHOLD, main._WAKE_MODE, main._AWAKE_SEC, main._IGNORE_SPEAKERS = old


def test_edge_voice_read_per_call(monkeypatch):
    from telegram_bot import tts_edge
    monkeypatch.setenv("EDGE_VOICE", "ru-RU-SvetlanaNeural")
    assert tts_edge._voice(None) == "ru-RU-SvetlanaNeural" and tts_edge._voice("x") == "x"


def test_sensitivity_scale():
    import ui_settings as U
    assert U.slider_to_threshold(100) == U.TH_MIN and U.slider_to_threshold(0) == U.TH_MAX
    for th in (40, 150, 300, 700):
        assert abs(U.slider_to_threshold(U.threshold_to_slider(th)) - th) / th < 0.05
    assert U.sensitivity_word(90) == "очень высокая" and U.sensitivity_word(10) == "низкая"


@pytest.fixture
def page(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(S, "devices", lambda kind: ["Микрофон (Realtek)", "Гарнитура (AirPods)"] if kind == "input"
                        else ["Динамики (Realtek)", "Наушники (AirPods)"])
    monkeypatch.setattr("core.paths.save_api_keys", lambda d: True)
    import ui_settings as U
    d = U.SettingsDialog(None, probe=False)
    d.show()
    app.processEvents()
    yield d, app
    d.hide()
    d.deleteLater()


def test_settings_page_saves_immediately(page):
    d, app = page
    assert d.mic.currentText().startswith("Автовыбор") and d.mic.count() == 3
    d.mic.setCurrentIndex(d.mic.findData("Гарнитура (AirPods)"))
    d.spk.setCurrentIndex(d.spk.findData("Наушники (AirPods)"))
    d.wake.setCurrentIndex(d.wake.findData("always_on"))
    d.awake.setCurrentIndex(d.awake.findData(60))
    d.camera.setCurrentIndex(d.camera.findData(1))
    d.edge.setCurrentIndex(d.edge.findData("ru-RU-SvetlanaNeural"))
    d.ignore.setChecked(False)
    d.sens.setValue(90)
    d.sens.sliderReleased.emit()
    v = S.load()
    assert (v["mic"], v["speaker"], v["wake_mode"], v["awake_sec"], v["camera"], v["edge_voice"]) == \
        ("Гарнитура (AirPods)", "Наушники (AirPods)", "always_on", 60, 1, "ru-RU-SvetlanaNeural")
    assert v["ignore_speakers"] is False and v["mic_threshold"] < 100
    assert d.saved.text().startswith("✓") and d.meter.threshold == v["mic_threshold"]
    d.island.setChecked(False)
    assert "после перезапуска" in d.saved.text()                   # капсула — после перезапуска
    import ui_anim
    d.anims.setChecked(False)
    assert os.environ["JARVIS_ANIMATIONS"] == "0" and not ui_anim.enabled()   # сразу, без перезапуска
    d.anims.setChecked(True)
    assert ui_anim.enabled()
    d.meter.set_level(500)
    d.meter.grab()


def test_settings_page_keeps_unplugged_device(page, monkeypatch):
    d, app = page
    S.set("mic", "Старый USB-микрофон")
    import ui_settings as U
    d2 = U.SettingsDialog(None, probe=False)
    assert d2.mic.currentData() == "Старый USB-микрофон" and "не подключён" in d2.mic.currentText()
    d2.deleteLater()


def test_settings_saved_by_notepad_with_bom_are_read(tmp_path, monkeypatch):
    """Блокнот пишет UTF-8 с BOM — раньше такие настройки молча сбрасывались."""
    p = tmp_path / "settings.json"
    p.write_bytes('﻿{"wake_mode": "always_on", "awake_sec": 120}'.encode("utf-8"))
    monkeypatch.setenv("JARVIS_SETTINGS", str(p))
    assert S.get("wake_mode") == "always_on"
    S.set("briefing", False)
    import json
    assert json.loads(p.read_text(encoding="utf-8-sig")) == {
        "wake_mode": "always_on", "awake_sec": 120, "briefing": False}


def test_broken_settings_are_kept_aside_not_overwritten(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    p.write_text('{"wake_mode": "always_on",,}', encoding="utf-8")
    monkeypatch.setenv("JARVIS_SETTINGS", str(p))
    S.set("briefing", False)
    broken = list(tmp_path.glob("settings.json.broken-*"))
    assert broken and "always_on" in broken[0].read_text(encoding="utf-8")
