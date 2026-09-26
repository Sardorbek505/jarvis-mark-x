"""Глаза Джарвиса: кадры уходят в Live только при изменении картинки, в
тишине реже, через 20 минут без разговора глаза закрываются сами; «следи и
скажи» зовёт Джарвиса, когда условие выполнено."""
import threading
import time

from PIL import Image

from core import eyes as ey


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _img(shade: int):
    return Image.new("RGB", (320, 180), (shade, shade, shade))


def _eyes(frames, talking=True):
    clock = Clock()
    sent = []
    state = {"talking": talking}
    e = ey.Eyes(send=lambda jpeg: sent.append(jpeg) or True, active=lambda: state["talking"],
                grab=lambda source: frames[0], now=clock)
    e.source = "screen"
    return e, clock, sent, state


def test_sends_only_on_change_or_keepalive():
    frames = [_img(10)]
    e, clock, sent, state = _eyes(frames)
    assert e.step() and len(sent) == 1                  # первый кадр — сразу
    clock.t += 1
    assert not e.step()                                 # не изменилось — не шлём
    frames[0] = _img(200)
    clock.t += 1
    assert e.step() and len(sent) == 2                  # изменилось — шлём
    clock.t += ey.KEEPALIVE_ACTIVE + 0.1
    assert e.step()                                     # в разговоре — не реже, чем раз в 6 с
    state["talking"] = False
    clock.t += ey.KEEPALIVE_ACTIVE + 0.1
    assert not e.step()                                 # в тишине пульс реже
    clock.t += ey.KEEPALIVE_IDLE
    assert e.step()
    assert sent[0][:2] == b"\xff\xd8"                   # это JPEG


def test_closes_itself_after_long_silence():
    e, clock, sent, state = _eyes([_img(10)], talking=False)
    closed = threading.Event()
    e.on_change = lambda src: src == "off" and closed.set()
    e.step()
    clock.t += ey.AUTO_OFF_SEC + 1
    assert not e.step()
    assert closed.wait(2) and e.source == "off"


def test_open_close_status_and_indicator(monkeypatch):
    changes = []
    e = ey.Eyes(send=lambda j: True, grab=lambda s: _img(50))
    e.on_change = changes.append
    monkeypatch.setattr(ey, "IDLE_INTERVAL", 0.01)
    assert "экран" in e.open("screen") and e.source == "screen"
    assert e.status() == "Смотрю на экран."
    time.sleep(0.05)
    assert e.sent >= 1
    assert e.close() == "Больше не смотрю, сэр." and e.source == "off"
    assert changes == ["screen", "off"]
    assert e.close() == "Я и так не смотрю, сэр."


def test_send_failure_keeps_trying_same_frame():
    """Связь моргнула — кадр не засчитан отправленным, уйдёт на следующем шаге."""
    ok = {"v": False}
    e = ey.Eyes(send=lambda j: ok["v"], active=lambda: True, grab=lambda s: _img(10), now=Clock())
    e.source = "screen"
    assert not e.step() and e.sent == 0
    ok["v"] = True
    assert e.step() and e.sent == 1


def test_watch_speaks_when_condition_met():
    said = []
    done = threading.Event()
    e = ey.Eyes(grab=lambda s: _img(10))
    e.say = lambda text: (said.append(text), done.set())
    answers = iter([False, False, True])
    assert e.watch("загрузка дошла до 100%", check=lambda jpeg, c: next(answers), every=0.01) == \
        "Слежу, сэр. Скажу, когда загрузка дошла до 100%."
    assert e.status() == "Глаза закрыты. Слежу, когда: загрузка дошла до 100%."
    assert done.wait(2)
    assert "загрузка дошла до 100%" in said[0]
    time.sleep(0.05)
    assert e.watch_condition == ""


def test_watch_can_be_stopped():
    e = ey.Eyes(grab=lambda s: _img(10))
    e.say = lambda text: (_ for _ in ()).throw(AssertionError("не должен говорить"))
    e.watch("письмо", check=lambda j, c: False, every=0.01)
    assert e.stop_watch() == "Перестал следить."
    time.sleep(0.05)
    assert e.stop_watch() == "Я ни за чем не слежу."


def test_change_metric():
    a, b = ey.fingerprint(_img(0)), ey.fingerprint(_img(255))
    assert ey.changed(a, a) == 0 and ey.changed(a, b) == 1.0 and ey.changed(None, a) == 1.0


def test_island_shows_eye(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui_island as ui
    w = ui.Island(poll=False)
    try:
        w.set_eyes(True)
        app.processEvents()
        assert w.model.eyes is True
        w.set_wanted(True)
        for _ in range(40):
            w._step()
            app.processEvents()
            time.sleep(0.01)
        w.repaint()
    finally:
        w.close()
