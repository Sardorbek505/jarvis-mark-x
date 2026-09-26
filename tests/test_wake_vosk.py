"""Локальное слово «Джарвис»: без vosk и модели, на поддельном распознавателе."""
import json
import threading
import time

from core.wake_vosk import LocalWake, has_wake_word


class _FakeRec:
    """Выдаёт заранее заданный текст по кадрам, как KaldiRecognizer."""

    def __init__(self, script):
        self.script = list(script)
        self.resets = 0

    def AcceptWaveform(self, pcm):
        return False

    def PartialResult(self):
        return json.dumps({"partial": self.script.pop(0) if self.script else ""})

    def Result(self):
        return json.dumps({"text": ""})

    def Reset(self):
        self.resets += 1


def _run(script, frames):
    heard = []
    done = threading.Event()
    rec = _FakeRec(script)

    def on_wake(text):
        heard.append(text)
        done.set()

    w = LocalWake(on_wake, recognizer_factory=lambda: rec)
    assert w.start()
    for _ in range(frames):
        w.feed(b"\0" * 2048)
    done.wait(1.0)
    time.sleep(0.1)
    w.stop()
    return heard, rec


def test_wakes_on_name():
    heard, rec = _run(["", "какая", "джарвис открой", "джарвис открой ютуб"], 4)
    assert heard == ["джарвис открой"]           # одно имя — одно пробуждение
    assert rec.resets >= 1


def test_ignores_speech_without_name():
    heard, _ = _run(["какая погода", "включи музыку", "черви в саду"], 3)
    assert heard == []


def test_feed_before_start_is_ignored():
    w = LocalWake(lambda t: None, recognizer_factory=lambda: _FakeRec([]))
    w.feed(b"\0" * 10)                            # не готов — не падает и не копит


def test_missing_model_falls_back():
    w = LocalWake(lambda t: None, model_dir=None)
    import core.wake_vosk as wv
    orig = wv.find_model_dir
    wv.find_model_dir = lambda: None
    try:
        assert w.start() is False and not w.ready
    finally:
        wv.find_model_dir = orig


def test_name_variants():
    for t in ("Джарвис", "жарвис", "джервис", "дарвис", "Jarvis", "джарвису"):
        assert has_wake_word(t), t
    for t in ("жевать", "черви", "дарвин", "сервис", "джаз"):
        assert not has_wake_word(t), t


# ─── Имя чужим письмом ───────────────────────────────────────────────────────
def test_name_written_in_another_script_still_wakes():
    """Живой журнал: человек сказал «Джарвис», Gemini записал «ჯარის»
    грузинскими буквами — и Джарвис промолчал, «обращались не ко мне».
    Расшифровка гуляет по письменностям (там же были китайский и тайский),
    поэтому имя надо узнавать и так."""
    assert has_wake_word("ჯარის")                    # ровно то, что было в журнале
    assert has_wake_word("ჯარვის")                   # полное написание
    assert has_wake_word("ჯარვის, როგორ ხარ")        # внутри фразы


def test_other_scripts_do_not_wake_on_anything():
    """Расширение не должно будить на любой чужой речи."""
    assert not has_wake_word("看到了。")
    assert not has_wake_word("ტელევიზორი ჩართე")     # грузинский без имени
    assert not has_wake_word("Türkçenin galosu mu sana?")
