"""«Джарвис» без ключей: sherpa-onnx keyword spotter (core/wake_kws.py)."""
import io
import tarfile
import time

import numpy as np

from core import wake_kws as K


class FakeSpotter:
    """Срабатывает, когда в звуке есть «громкий» кусок — как будто сказали слово."""

    def __init__(self):
        self.loud = False

    def create_stream(self):
        return self

    def accept_waveform(self, rate, x):
        assert rate == 16000 and x.dtype == np.float32
        self.loud = self.loud or float(np.abs(x).max()) > 0.5
        self._ready = True

    def is_ready(self, s):
        r, self._ready = getattr(self, "_ready", False), False
        return r

    def decode_stream(self, s):
        pass

    def get_result(self, s):
        return "JARVIS" if self.loud else ""

    def reset_stream(self, s):
        self.loud = False


def test_wake_fires_once_per_word_with_cooldown():
    heard = []
    w = K.KwsWake(heard.append, spotter_factory=FakeSpotter)
    assert w.start()
    quiet = np.zeros(1024, np.int16).tobytes()
    loud = (np.ones(1024) * 30000).astype(np.int16).tobytes()
    for pcm in (quiet, loud, quiet, loud, quiet):        # второе «слово» — в пределах паузы
        w.feed(pcm)
    time.sleep(0.5)
    w.stop()
    assert heard == ["джарвис"]


def test_keywords_cover_russian_pronunciation():
    words = [k.split("@")[1] for k in K.KEYWORDS]
    assert "JARVIS" in words and len(words) >= 4
    assert all(" " in k.split("@")[0].strip() for k in K.KEYWORDS)      # токены BPE, не сырой текст


def test_download_keeps_only_needed_files(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name in list(K.FILES.values()) + ["encoder-epoch-12-avg-2-chunk-16-left-64.onnx", "README.md"]:
            data = b"x" * 10
            info = tarfile.TarInfo(f"sherpa-onnx-kws/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    d = K.download(tmp_path / "kws", fetch=lambda url: buf.getvalue())
    assert sorted(p.name for p in d.iterdir()) == sorted(K.FILES.values())
    assert K.find_model() is None or K.find_model().is_dir()


def test_no_model_no_start(monkeypatch):
    monkeypatch.setattr(K, "find_model", lambda: None)
    assert K.KwsWake(lambda t: None).start() is False
