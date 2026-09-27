"""Узнавание голоса: запись 5 фраз → отпечаток и порог; опасное «да» —
только вашим голосом (или набранное с клавиатуры); нечем проверить — не
мешаем. Настоящие голоса (Vosk + синтез) — в CI, scripts/voice_id_check.py."""
import asyncio
import os
import random
import time
from types import SimpleNamespace

import pytest

from core import voice_id as V

SEC = 16000 * 2                                           # байт в секунде PCM


def speech(speaker: int, sec: float = 2.0, seed: int = 0) -> bytes:
    """«Речь» speaker: первый байт — кто говорит, остальное — шум для разброса."""
    rnd = random.Random(speaker * 1000 + seed)
    return bytes([speaker]) + bytes(rnd.getrandbits(8) for _ in range(int(sec * SEC) - 1))


BASE = {1: [1.0, 0.2, 0.1, 0.0], 2: [0.1, 1.0, 0.3, 0.2], 3: [0.2, 0.1, 1.0, 0.4]}


def fake_embed(pcm: bytes):
    rnd = random.Random(pcm[1:64])
    return [x + rnd.uniform(-0.15, 0.15) for x in BASE[pcm[0]]]


@pytest.fixture
def vid(tmp_path):
    return V.VoiceID(tmp_path / "voice_id.json", embed=fake_embed)


def test_enroll_then_verify(vid, tmp_path):
    assert not vid.enrolled() and vid.verify(speech(1)) == (None, 0.0)
    res = vid.enroll([speech(1, 4, s) for s in range(5)])
    assert res["ok"] and res["n"] == 5 and 0.42 <= vid.threshold <= 0.55
    assert vid.threshold_for(1 * SEC) == pytest.approx(vid.threshold - 0.08)               # коротко — мягче
    assert vid.threshold_for(5 * SEC) == vid.threshold
    ok, score = vid.verify(speech(1, 3, seed=99))
    assert ok is True and score > 0.9
    assert vid.verify(speech(2, 3))[0] is False and vid.verify(speech(3, 3))[0] is False
    assert V.VoiceID(tmp_path / "voice_id.json", embed=fake_embed).enrolled()      # сохранился
    assert vid.verify(speech(1, 0.5)) == (None, 0.0)                             # мало речи — нечем проверить


def test_enroll_needs_enough_speech(vid):
    res = vid.enroll([speech(1, 0.3, s) for s in range(5)])
    assert not res["ok"] and "Мало речи" in res["text"] and not vid.enrolled()


def test_reset(vid):
    vid.enroll([speech(1, 4, s) for s in range(5)])
    vid.reset()
    assert not vid.enrolled() and not vid.path.exists()


def test_what_is_sensitive():
    assert V.sensitive("contacts", {"action": "message"}) and V.sensitive("contacts", {"action": "call"})
    assert not V.sensitive("contacts", {"action": "read"})
    assert V.sensitive("computer_control", {"action": "shutdown"}) and not V.sensitive("computer_control",
                                                                                     {"action": "volume_up"})
    assert V.sensitive("files", {"action": "delete"}) and not V.sensitive("music_player", {"action": "play"})


def test_math():
    assert V.cosine([1, 0], [1, 0]) == 1.0 and V.cosine([1, 0], [0, 1]) == 0.0 and V.cosine([0, 0], [1, 1]) == 0.0
    assert V.mean([[0, 2], [2, 4]]) == [1, 3]


# ── в Джарвисе ────────────────────────────────────────────────────────────────

@pytest.fixture
def jarvis(tmp_path, monkeypatch, vid):
    import main
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(V, "_vid", vid)
    logs = []
    ui = SimpleNamespace(muted=False, on_text_command=None, write_log=logs.append, set_state=lambda s: None,
                         lock_on=lambda t: None)
    j = main.Jarvis(ui)
    return j, logs


def talk(j, pcm: bytes):
    now = time.monotonic()
    for i in range(0, len(pcm), 2048):
        j._voice_ring.append((now, pcm[i:i + 2048]))


def test_owner_yes_passes_stranger_yes_blocked(jarvis, vid):
    j, logs = jarvis
    vid.enroll([speech(1, 4, s) for s in range(5)])
    talk(j, speech(1, 3, seed=7))
    assert asyncio.run(j._voice_denied("contacts", {"action": "message"})) == ""
    j._voice_ring.clear()
    talk(j, speech(2, 3))
    denied = asyncio.run(j._voice_denied("contacts", {"action": "message"}))
    assert denied.startswith("НЕ ВЫПОЛНЕНО — голос не похож") and any("🔒" in m for m in logs)
    assert asyncio.run(j._voice_denied("music_player", {"action": "play"})) == ""     # не опасное — без проверки


def test_typed_yes_is_trusted_and_no_profile_means_no_check(jarvis, vid):
    j, _ = jarvis
    vid.enroll([speech(1, 4, s) for s in range(5)])
    talk(j, speech(2, 3))
    j._on_text_command("да")                                                     # набрали с клавиатуры
    assert asyncio.run(j._voice_denied("contacts", {"action": "call"})) == ""
    j._user_turn += 1                                                            # следующая реплика — голосом
    assert asyncio.run(j._voice_denied("contacts", {"action": "call"})) != ""
    vid.reset()
    assert asyncio.run(j._voice_denied("contacts", {"action": "call"})) == ""    # голос не записан — как раньше


def test_stranger_cannot_confirm_shutdown(jarvis, vid, monkeypatch):
    j, _ = jarvis
    vid.enroll([speech(1, 4, s) for s in range(5)])
    ran = []
    monkeypatch.setattr("main.computer_settings", lambda parameters, player=None: ran.append(1) or "ok")
    fc = SimpleNamespace(id="1", name="computer_control", args={"action": "shutdown"})
    asyncio.run(j._execute_tool(fc))                                             # «точно?»
    j._user_turn += 1
    j.last_user_text = "да"
    talk(j, speech(3, 3))                                                        # «да» сказал гость
    r = asyncio.run(j._execute_tool(fc))
    assert ran == [] and "голос не похож" in r.response["result"]


# ── окно ─────────────────────────────────────────────────────────────────────

def test_enroll_window(vid):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui_about
    takes = iter(range(10))
    dlg = ui_about.VoiceEnrollDialog(vid=vid, record=lambda sec: speech(1, sec, next(takes)))
    try:
        for _ in range(len(V.ENROLL_PHRASES)):
            assert dlg.btn.isEnabled()
            dlg.take()
            for _ in range(200):
                app.processEvents()
                if dlg.btn.isEnabled():
                    break
                time.sleep(0.01)
        for _ in range(200):
            app.processEvents()
            if vid.enrolled() and dlg.btn.text() == "Закрыть":
                break
            time.sleep(0.01)
        assert vid.enrolled() and "Голос записан" in dlg.status.text()
    finally:
        dlg.close()
    about = ui_about.AboutDialog(vid=vid)
    try:
        assert "Голос записан" in about.voice_title.text() and about.voice_reset.isVisibleTo(about)
        about.voice_forget()
        assert not vid.enrolled() and "не записан" in about.voice_title.text()
    finally:
        about.close()
