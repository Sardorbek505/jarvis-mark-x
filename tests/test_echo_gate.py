"""Голос поверх музыки (core/echo_gate.py) и микшер без «самодеятельности»
(core/ducking_controller.py): молчащие программы не приглушаются, приглушение
музыки не открывает дорогу самой музыке в микрофон."""
import random

from core.echo_gate import EchoGate


def _learn(g: EchoGate, coupling=800.0, level=0.5, n=60, seed=1):
    rnd = random.Random(seed)
    for _ in range(n):
        lv = level * rnd.uniform(0.6, 1.0)
        g.observe(coupling * lv * rnd.uniform(0.8, 1.2), lv)


def test_no_music_uses_plain_threshold():
    g = EchoGate()
    assert g.is_voice(200, 0.0, 150) and not g.is_voice(100, 0.0, 150) and not g.music(0.01)


def test_cold_start_is_careful():
    g = EchoGate()
    assert g.coupling() is None and not g.is_voice(5000, 0.5, 150)     # ещё не знаем комнату


def test_echo_blocked_voice_passes_and_ducking_does_not_open_the_door():
    g = EchoGate()
    _learn(g)
    c = g.coupling()
    assert 600 < c < 900
    assert not g.is_voice(800 * 0.5, 0.5, 150)                         # сама музыка — нет
    assert g.is_voice(800 * 0.5 + 3000, 0.5, 150)                      # голос поверх — да
    # Музыку приглушили в 5 раз: уровень колонок и эхо упали вместе —
    # эхо по-прежнему не «голос» (раньше тут круг и замыкался).
    assert not g.is_voice(800 * 0.1, 0.1, 150)
    assert g.is_voice(800 * 0.1 + 900, 0.1, 150)


def test_occasional_speech_does_not_spoil_learning():
    g = EchoGate()
    rnd = random.Random(2)
    for i in range(200):
        lv = 0.4 * rnd.uniform(0.6, 1.0)
        rms = 800 * lv * rnd.uniform(0.8, 1.2) + (4000 if i % 7 == 0 else 0)   # иногда говорят
        g.observe(rms, lv)
    assert 500 < g.coupling() < 1000


# ── микшер ────────────────────────────────────────────────────────────────────
class _Ctl:
    def __init__(self, v):
        self.v = v

    def GetMasterVolume(self):
        return self.v

    def SetMasterVolume(self, v, ctx):
        self.v = v


class _Meter:
    def __init__(self, peak):
        self.peak = peak

    def GetPeakValue(self):
        return self.peak


def test_only_sounding_apps_are_ducked(tmp_path):
    import time

    from test_voice_trigger_engine import _silent_controller, _wait
    spotify, discord, game = _Ctl(0.9), _Ctl(1.0), _Ctl(0.7)
    apps = {1: ("spotify.exe", spotify), 2: ("discord.exe", discord), 3: ("cs2.exe", game)}
    dc = _silent_controller(attack_ms=10, release_ms=10, state_path=str(tmp_path / "d.json"))
    dc._sessions = lambda: [(pid, name, ctl) for pid, (name, ctl) in apps.items()]
    dc._session_meters = {1: _Meter(0.3), 2: _Meter(0.0)}             # у игры замера нет — считаем, что играет
    try:
        dc.duck("тест")
        assert _wait(lambda: spotify.v < 0.3 and game.v < 0.3)
        time.sleep(0.1)
        assert discord.v == 1.0                                          # молчит — ползунок не тронут
        dc.set_state(__import__("core.ducking_controller", fromlist=["x"]).DuckingState.RESTORING)
        assert _wait(lambda: spotify.v == 0.9 and game.v == 0.7)
    finally:
        dc.close()
