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


def test_name_heard_with_l_instead_of_v():
    """Живой тест с владельцем: он сказал «Джарвис», расшифровка дала
    «Ты меня слышишь, Чарлис?» — «л» вместо «в», и Джарвис промолчал."""
    assert has_wake_word("Ты меня слышишь, Чарлис?")
    assert has_wake_word("чарлис")
    assert has_wake_word("джарлис")
    assert not has_wake_word("Карлос уехал")
    assert not has_wake_word("парус")


# ── имя, выученное на голосе владельца ───────────────────────────────────────
import core.wake_vosk as wv  # noqa: E402


def test_learn_aliases_keeps_repeated_name_sounds_only():
    names = [["дар вис", "дар"], ["дар вис"], ["жар вис", "дар вис"], ["из"], ["дар вис кто"], ["шар"]]
    negatives = ["какая сегодня погода", "сегодня очень жарко", "дарвин написал книгу", "кто там"]
    got = wv.learn_aliases(names, negatives)
    assert "дар вис" in got and "вис" in got        # повторились в записях имени
    assert "из" not in got                          # короткое частое слово — будило бы на всём
    assert "кто" not in got and "шар" not in got     # было в обычных фразах / лишь однажды


def test_alias_match_is_whole_words():
    assert wv.matches_alias("Эй, дар вис, включи", ["дар вис"])
    assert not wv.matches_alias("дарвин", ["дар"])


def test_local_wake_uses_learned_aliases():
    heard = []
    rec = _FakeRec(["", "дар вис"])
    w = LocalWake(heard.append, recognizer_factory=lambda: rec, aliases=["дар вис"])
    assert w.start()
    for _ in range(5):
        w.feed(b"\0" * 10)
    end = time.monotonic() + 2
    while not heard and time.monotonic() < end:
        time.sleep(0.01)
    w.stop()
    assert heard == ["дар вис"]


class _TagRec:
    """«Распознаватель»: что слышит, зависит от метки в записи."""

    def __init__(self, heard_by_tag, grammar=None):
        self.map, self.grammar, self.got = heard_by_tag, grammar, b""

    def AcceptWaveform(self, pcm):
        self.got += pcm
        return False

    def PartialResult(self):
        return json.dumps({"partial": ""})

    def FinalResult(self):
        tag = next((t for t in self.map if t in self.got), None)
        text = self.map.get(tag, "")
        if self.grammar is not None and not wv.matches_alias(text, self.grammar):
            text = "[unk]"
        return json.dumps({"text": text, "alternatives": [{"text": text}]})

    def SetMaxAlternatives(self, n):
        pass


def test_calibrate_learns_and_checks_on_same_takes(monkeypatch):
    heard = {b"N1": "дар вис", b"N2": "дар вис", b"N3": "жар вис", b"X1": "погода", b"X2": "жарко"}
    monkeypatch.setattr(wv, "_free_recognizer", lambda model: _TagRec(heard))
    rep = wv.calibrate(None, [b"N1", b"N2", b"N3"], [b"X1", b"X2"])
    assert "дар вис" in rep["aliases"] and "погода" not in rep["aliases"]
    assert rep["hits"] == 3 and rep["false"] == 0


def test_calibration_saves_and_enables_local_wake(tmp_path, monkeypatch):
    import main as jarvis_main
    from core import wake_calibrate
    monkeypatch.setenv("JARVIS_WAKE_ALIASES", str(tmp_path / "w.json"))
    monkeypatch.delenv("JARVIS_LOCAL_WAKE", raising=False)
    assert jarvis_main._local_wake_enabled() is False            # без калибровки — Gemini
    monkeypatch.setattr(wv, "calibrate", lambda model, n, x: {"aliases": ["дар вис"], "names": 8,
                                                             "negatives": 5, "hits": 7, "false": 0})
    rep = wake_calibrate.run_calibration([b""], [b""], model=object())
    assert rep["saved"] and "7 из 8" in wake_calibrate.describe(rep)
    assert wv.load_aliases() == ["дар вис"]
    assert jarvis_main._local_wake_enabled() is True             # после — на компьютере
    monkeypatch.setenv("JARVIS_LOCAL_WAKE", "0")
    assert jarvis_main._local_wake_enabled() is False            # выключатель сильнее


def test_bad_calibration_is_not_saved(tmp_path, monkeypatch):
    """Слабая самопроверка — не включать: пусть имя ищет Gemini."""
    from core import wake_calibrate
    monkeypatch.setenv("JARVIS_WAKE_ALIASES", str(tmp_path / "w.json"))
    for rep in ({"aliases": ["дар"], "names": 8, "negatives": 5, "hits": 4, "false": 0},
                {"aliases": ["дар"], "names": 8, "negatives": 5, "hits": 8, "false": 1}):
        monkeypatch.setattr(wv, "calibrate", lambda model, n, x, r=rep: dict(r))
        got = wake_calibrate.run_calibration([b""], [b""], model=object())
        assert not got["saved"] and "Пока не включаю" in wake_calibrate.describe(got)
    assert wv.load_aliases() == []
