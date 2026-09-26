"""Громкость и яркость: точные действия, разбор значения, настоящий уровень."""
import sys
import types

import pytest

from actions import computer_settings as cs


class _Ep:
    def __init__(self, level=0.4, muted=1):
        self.level, self.muted = level, muted

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, v, ctx):
        self.level = v

    def SetMute(self, m, ctx):
        self.muted = m


@pytest.fixture
def win(monkeypatch):
    ep = _Ep()
    monkeypatch.setattr(cs, "_OS", "Windows")
    monkeypatch.setattr(cs, "_endpoint", lambda: ep)
    monkeypatch.setitem(sys.modules, "comtypes", types.SimpleNamespace(
        CoInitialize=lambda: None, CoUninitialize=lambda: None))
    return ep


@pytest.mark.parametrize("raw,want", [
    ("50", 50), ("50%", 50), ("на 10", 10), ("до 30 процентов", 30),
    ("максимум", 100), ("минимум", 0), ("половина", 50), ("", None), ("150", 100),
])
def test_parse_level(raw, want):
    assert cs.parse_level(raw) == want


def test_set_volume_is_exact_not_plus(win):
    # «громкость 50» раньше превращалось в «+50%»
    assert cs.computer_settings({"action": "volume_set", "value": "50"}) == "Громкость 50%."
    assert win.level == 0.5


def test_up_down_from_real_level(win):
    assert cs.computer_settings({"action": "volume_up", "value": ""}) == "Громкость 50%."
    assert cs.computer_settings({"action": "volume_down", "value": "на 20"}) == "Громкость 30%."


def test_louder_unmutes(win):
    cs.computer_settings({"action": "volume_up"})
    assert win.muted == 0


def test_mute_is_explicit_not_toggle(win):
    cs.computer_settings({"action": "mute"})
    cs.computer_settings({"action": "mute"})
    assert win.muted == 1                     # дважды «выключи звук» — всё ещё выключен
    assert cs.computer_settings({"action": "unmute"}).startswith("Звук включён")


def test_russian_phrases_still_work(win):
    assert cs.computer_settings({"action": "сделай громче"}) == "Громкость 50%."
    assert cs.computer_settings({"action": "громкость", "value": "80"}) == "Громкость 80%."


def test_brightness_down_words_go_down(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "_OS", "Windows")
    monkeypatch.setattr(cs, "_brightness_windows", lambda m, s, t: calls.append((m, s, t)) or "ok")
    cs.computer_settings({"action": "brightness_down", "value": "10"})
    cs.computer_settings({"action": "сделай темнее"})
    cs.computer_settings({"action": "brightness_set", "value": "30%"})
    assert calls == [("down", 10, 10), ("down", 10, None), ("set", 30, 30)]


def test_sbc_brightness(monkeypatch):
    state = {"v": 70}
    fake = types.SimpleNamespace(get_brightness=lambda: [state["v"]],
                                 set_brightness=lambda v: state.update(v=v))
    monkeypatch.setitem(sys.modules, "screen_brightness_control", fake)
    assert cs._brightness_windows("down", 20, None) == "Яркость 50%."
    assert state["v"] == 50
