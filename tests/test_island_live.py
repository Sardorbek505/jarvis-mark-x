"""Капсула 2.0: лицо с эмоциями, шаги задачи, кнопки разрешения, файл на капсулу.

Модель — без экрана: что показать и какое лицо. Окно — в Qt без экрана:
клики по «Разрешить / Отклонить», бросок файла, лицо меняет форму глаз
через моргание."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui_island as ui  # noqa: E402

# ── шаги задачи ──────────────────────────────────────────────────────────────


def test_tool_labels_are_human():
    assert ui.tool_label("open_app", {"app_name": "Telegram"}) == "Открываю Telegram"
    assert ui.tool_label("web_search", {"query": "курс доллара"}) == "Ищу: курс доллара"
    assert ui.tool_label("contacts", {"action": "message", "name": "Ибрагим"}) == "Пишу Ибрагим"
    assert ui.tool_label("contacts", {"action": "call", "name": "Мама"}) == "Звоню Мама"
    assert ui.tool_label("look_at_screen", {}) == "Смотрю на экран"
    assert ui.tool_label("unknown_tool", {}) == "Unknown tool"
    assert ui.tool_label("web_search", {"query": "x" * 200}).endswith("…")      # длинное — обрезано


def test_task_counts_steps_and_ends_happy():
    m = ui.IslandModel()
    m.tool_start("open_app", {"app_name": "Telegram"}, now=0)
    assert m.mode(False, now=0) == "task"
    m.tool_end("open_app", True, now=0.5)
    m.tool_start("contacts", {"action": "message", "name": "Ибрагим"}, now=0.7)   # та же просьба
    t = m.task_view(now=0.8)
    assert len(t.steps) == 2 and t.done == 1 and t.running
    assert t.current().label == "Пишу Ибрагим"
    assert m.emotion(now=0.8) != "happy"              # шаг кончился, но задача ещё идёт
    m.tool_end("contacts", True, now=1.0)
    assert m.emotion(now=1.1) == "happy"              # всё сделано — рад
    assert m.task_view(now=1.0 + ui.TASK_HOLD_SEC - 0.1) is not None   # «Готово» ещё висит
    assert m.task_view(now=1.0 + ui.TASK_HOLD_SEC + 0.1) is None
    assert m.mode(False, now=10) == "compact"


def test_failed_step_makes_face_sad_and_new_request_starts_new_task():
    m = ui.IslandModel()
    m.tool_start("files", {"action": "open", "path": "a.txt"}, now=0)
    m.tool_end("files", False, now=1)
    assert m.task.failed and m.emotion(now=1.2) == "sad"
    m.tool_start("weather", {"city": "Ташкент"}, now=1 + ui.TASK_JOIN_SEC + 0.5)  # новая просьба
    assert [s.tool for s in m.task.steps] == ["weather"]


def test_step_waiting_for_yes_is_not_a_failure():
    m = ui.IslandModel()
    m.tool_start("computer_control", {"action": "shutdown"}, now=0)
    m.tool_end("computer_control", None, now=0.2)
    assert m.task.steps[0].status == "wait" and not m.task.failed
    assert m.emotion(now=0.3) == "calm"                # ни радости, ни грусти


# ── разрешение, файл, сбой ───────────────────────────────────────────────────

def test_confirm_beats_hover_and_expires():
    m = ui.IslandModel()
    m.ask_confirm("Выключить компьютер?", now=0)
    assert m.mode(True, now=1) == "confirm"            # мышь пришла нажать кнопку — не закрываем
    assert m.emotion(now=1) == "alert"
    assert not m.quiet(now=1)
    assert m.mode(False, now=ui.CONFIRM_SEC + 1) == "compact"   # main тоже забыл вопрос


def test_drop_and_upload_flow():
    m = ui.IslandModel()
    m.drop_hover = True
    assert m.mode(True, now=0) == "drop" and m.emotion(now=0) == "hungry"
    m.drop_hover = False
    m.set_upload("quote.pdf", 0.4, "Читаю", now=0)
    assert m.mode(False, now=0.1) == "upload" and m.emotion(now=0.1) == "think"
    m.set_upload("quote.pdf", 1.0, "Отправил", now=1)
    assert m.upload_view(now=1.5) is not None
    assert m.upload_view(now=2.5) is None              # сама уходит


def test_upload_failure_shows_trouble():
    m = ui.IslandModel()
    m.set_upload("game.exe", -1, "не умею читать .exe", now=0)
    b = m.banner(now=0.1)
    assert m.upload is None and b.kind == "error" and ".exe" in b.text
    assert m.mode(False, now=0.1) == "banner" and m.emotion(now=0.1) == "dizzy"


def test_emotion_follows_state():
    m = ui.IslandModel()
    for st, emo in (("LISTENING", "listen"), ("THINKING", "think"), ("SPEAKING", "talk"),
                    ("RECONNECTING", "sad"), ("IDLE", "calm")):
        m.set_state(st)
        assert m.emotion(now=0) == emo, st
    m.state = "muted"
    assert m.emotion(now=0) == "sleep"


# ── лицо ─────────────────────────────────────────────────────────────────────

def test_face_switches_eye_shape_through_a_blink():
    from ui_face import Face
    f = Face(seed=1)
    f.set_emotion("happy")
    assert f.family == "dot"                           # не мгновенно
    closed = False
    for _ in range(30):
        f.step(0.016)
        closed |= f.open < 0.2
    assert closed and f.family == "arc" and f.open == 1.0


def test_face_blinks_by_itself_and_looks_at_cursor():
    from ui_face import Face
    f = Face(seed=2)
    f.look_at(5, -5)                                   # за пределами — прижато к краю
    blinks, was_open = 0, True
    for _ in range(int(12 / 0.016)):
        f.step(0.016)
        if was_open and f.open < 0.5:
            blinks += 1
        was_open = f.open >= 0.5
    assert blinks >= 2
    assert f.look[0] == pytest.approx(1.0, abs=0.01) and f.look[1] == pytest.approx(-1.0, abs=0.01)
    f.set_emotion("think")                             # у «думает» взгляд свой
    for _ in range(60):
        f.step(0.016)
    assert f.look[1] < -0.7


def test_face_paints_every_emotion():
    from PyQt6.QtGui import QImage, QPainter
    from PyQt6.QtWidgets import QApplication
    from ui_face import EMOTIONS, Face
    app = QApplication.instance() or QApplication([])  # noqa: F841  (без ссылки приложение умрёт)
    img = QImage(80, 80, QImage.Format.Format_ARGB32)
    for name in EMOTIONS:
        f = Face()
        f.set_emotion(name)
        for _ in range(20):
            f.step(0.02, 0.5)
        img.fill(0)
        p = QPainter(img)
        f.paint(p, 40, 40, 36)
        p.end()
        assert img.pixelColor(40, 30).alpha() > 0, name


# ── окно ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def island():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    got = {"confirm": [], "file": []}
    w = ui.Island(poll=False, on_confirm=got["confirm"].append, on_file=got["file"].append)
    yield w, app, got
    w.hide()
    w.deleteLater()


def _settle(w, app, sec=1.0):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        w._step()
        app.processEvents()
        time.sleep(0.01)


def _click(w, rect):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    pos = rect.center()
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(pos), QPointF(pos),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    w.mouseReleaseEvent(ev)


def test_allow_and_deny_buttons(island):
    w, app, got = island
    w.set_wanted(True)
    w.ask_confirm("Удалить a.txt?")
    _settle(w, app, 1.3)
    assert w._view == "confirm" and {"allow", "deny"} <= set(w._buttons)
    w.grab()                                           # нарисовать — кнопки на месте
    _click(w, w._buttons["allow"])
    assert got["confirm"] == [True] and w.model.confirm is None
    w.ask_confirm("Выключить компьютер?")
    _settle(w, app, 1.3)
    w.grab()
    _click(w, w._buttons["deny"])
    assert got["confirm"] == [True, False]


def test_steps_from_any_thread_reach_the_capsule(island):
    import threading
    w, app, _ = island
    w.set_wanted(True)
    th = threading.Thread(target=lambda: (w.tool_started("open_app", {"app_name": "Spotify"}),
                                          w.tool_finished("open_app", True)))
    th.start()
    th.join()
    _settle(w, app, 0.3)
    t = w.model.task_view()
    assert t and t.steps[0].label == "Открываю Spotify" and t.steps[0].status == "ok"


def test_dropping_a_file_hands_the_path_over(island, tmp_path):
    from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent
    w, app, got = island
    w.set_wanted(True)
    f = tmp_path / "quote.txt"
    f.write_text("Итого: 1 240 €", encoding="utf-8")
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(f))])
    pos = QPointF(w.W / 2, 10)
    enter = QDragEnterEvent(pos.toPoint(), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    w.dragEnterEvent(enter)
    assert enter.isAccepted() and w.model.drop_hover
    assert w.model.mode(False) == "drop"
    drop = QDropEvent(pos, Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    w.dropEvent(drop)
    assert got["file"] == [str(f)] and not w.model.drop_hover
    assert w.model.upload and w.model.upload.name == "quote.txt"


def test_without_file_handler_drops_are_ignored():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841  (без ссылки приложение умрёт)
    w = ui.Island(poll=False)
    assert not w.acceptDrops()
    w.deleteLater()


def test_orb_instead_of_face_when_disabled(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841  (без ссылки приложение умрёт)
    monkeypatch.setenv("JARVIS_ISLAND_FACE", "0")
    w = ui.Island(poll=False)
    assert w._face is None
    w.set_wanted(True)
    w._step()
    w.grab()                                           # рисуется шаром без ошибок
    w.hide()
    w.deleteLater()


def test_running_task_beats_reply_but_listening_beats_finished_task():
    m = ui.IslandModel()
    m.tool_start("open_app", {"app_name": "YouTube"}, now=0)
    m.notify("ДЖАРВИС", "Сейчас открою, сэр.", "reply", now=0.1)
    assert m.mode(False, now=0.2) == "task"             # прогресс не прячется за репликой
    m.notify("ЗВОНОК", "Мама звонит", now=0.2)
    m.banners = [b for b in m.banners if b.kind != "reply"]
    assert m.mode(False, now=0.3) == "banner"            # событие важнее
    m.banners.clear()
    m.tool_end("open_app", True, now=0.5)
    m.set_state("LISTENING")
    assert m.mode(False, now=0.6) == "listening"         # позвали — «Готово» уступает
