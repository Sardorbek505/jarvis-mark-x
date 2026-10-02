"""Слово «Джарвис» через Porcupine: файлы находятся сами, причина — по-человечески,
одно слово — одно срабатывание, сбой — назад к прежнему пути."""
import threading
import time

import numpy as np

from core import wake_porcupine as W


class _Engine:
    frame_length = 512

    def __init__(self, hit_at=()):
        self.n, self.hit_at, self.deleted = 0, set(hit_at), False

    def process(self, frame):
        assert len(frame) == 512
        self.n += 1
        return 0 if self.n in self.hit_at else -1

    def delete(self):
        self.deleted = True


def test_files_are_found_next_to_each_other(tmp_path, monkeypatch):
    for k in ("PICOVOICE_ACCESS_KEY", "PORCUPINE_KEYWORD", "PORCUPINE_MODEL"):
        monkeypatch.delenv(k, raising=False)
    wake = tmp_path / "wake"
    wake.mkdir()
    (wake / "Джарвис_ru_windows_v4_0_0.ppn").write_bytes(b"x")
    monkeypatch.setattr(W, "_wake_dir", lambda: wake)
    assert W.problem({"picovoice_access_key": "k"}).startswith("для русского слова нужен файл модели")
    (wake / "porcupine_params_ru.pv").write_bytes(b"x")
    access, kw, model = W.find_files({"picovoice_access_key": "k"})
    assert access == "k" and kw.endswith(".ppn") and model.endswith("porcupine_params_ru.pv")
    assert W.problem({"picovoice_access_key": "k"}) == ""
    assert W.problem({}) == "нет ключа Picovoice (AccessKey)"


def test_one_word_one_wake_and_frames_are_rebuffered():
    heard = []
    got = threading.Event()
    eng = _Engine(hit_at=(3, 4))                        # слово «держится» два кадра подряд
    w = W.PorcupineWake(lambda t: (heard.append(t), got.set()), engine_factory=lambda: eng)
    assert w.start()
    pcm = np.zeros(300, dtype=np.int16).tobytes()       # куски не кратны кадру
    for _ in range(12):
        w.feed(pcm)
    assert got.wait(3)
    time.sleep(0.2)
    assert heard == ["джарвис"]                         # пауза 1,5 с — второе не сработало
    w.stop()


def test_broken_engine_falls_back():
    def boom():
        raise RuntimeError("AccessKey is invalid")
    assert PorcupineWake_start(boom) is False


def PorcupineWake_start(factory):
    return W.PorcupineWake(lambda t: None, engine_factory=factory).start()


def test_keys_screen_has_wake_card():
    from core import keys
    s = keys.BY_ID["wake"]
    assert [f.key for f in s.fields] == ["picovoice_access_key", "porcupine_keyword", "porcupine_model"]
    status, msg = keys.check_wake({})
    assert status == "bad" and "ключ" in msg.lower()
