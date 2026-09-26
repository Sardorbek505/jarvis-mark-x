"""«Капсула» Джарвиса (как Dynamic Island), когда окно свёрнуто.

Модель — без экрана: какой вид показать, баннеры ответа и событий, таймер.
Окно — в Qt без экрана: появляется при сворачивании, прячется при
развороте и в полноэкранной игре, клики мимо капсулы проходят насквозь
(маска), кнопки плеера и «открыть» работают."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui_island as ui  # noqa: E402

# ── модель ───────────────────────────────────────────────────────────────────


def test_mode_priority_hover_banner_music_compact():
    m = ui.IslandModel()
    assert m.mode(False, now=0) == "compact"
    m.media = ui.Media("Macan — ASPHALT 8", "Macan", "music", True)
    assert m.mode(False, now=0) == "activity"
    m.media.playing = False
    assert m.mode(False, now=0) == "compact"                  # на паузе — не мельтешит
    m.notify("ЗВОНОК", "Вы не взяли трубку.", now=0)
    assert m.mode(False, now=1) == "banner"
    assert m.mode(True, now=1) == "expanded"                   # наведение важнее всего
    assert m.mode(False, now=ui.BANNER_SEC + 1) == "compact"   # баннер ушёл сам


def test_reply_banner_updates_in_place_and_lives_by_length():
    m = ui.IslandModel()
    m.notify("ДЖАРВИС", "В Ташкенте", "reply", now=0)
    m.notify("ДЖАРВИС", "В Ташкенте +18, ясно. Вечером похолодает до +12 — возьмите куртку, сэр.", "reply", now=1)
    assert len(m.banners) == 1 and m.banners[0].text.endswith("куртку, сэр.")   # дописался, не задвоился
    assert m.last_reply.startswith("В Ташкенте +18")
    assert m.banner(now=6) is not None                         # длинный ответ висит дольше 5,5 с
    for i in range(10):
        m.notify("СОБЫТИЕ", f"№{i}", now=2)
    assert len(m.banners) == 4                                 # очередь не растёт без предела


def test_states_and_timer():
    m = ui.IslandModel()
    for ui_state, s in (("LISTENING", "listening"), ("THINKING", "thinking"), ("SPEAKING", "speaking"),
                        ("RECONNECTING", "offline"), ("IDLE", "idle"), ("что-то", "idle")):
        m.set_state(ui_state)
        assert m.state == s
    m.timer_label, m.timer_end = "Сон", 1000 + 23 * 60 + 14
    assert m.timer_text(now_wall=1000) == "Сон · 23:14"
    m.timer_end = 1000 + 2 * 3600 + 5
    assert m.timer_text(now_wall=1000) == "Сон · 2:00:05"
    m.timer_label, m.timer_end = "Звонок в 06:00", 0
    assert m.timer_text() == "Звонок в 06:00"


# ── окно ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def island():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    opened = []
    w = ui.Island(on_open=lambda: opened.append(1), poll=False)
    yield w, app, opened
    w.hide()
    w.deleteLater()


def _settle(w, app, sec=1.0):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        w._step()
        app.processEvents()
        time.sleep(0.01)


def test_shows_only_when_wanted_and_not_over_fullscreen(island):
    w, app, _ = island
    assert not w.isVisible()
    w.set_wanted(True)
    assert w.isVisible() and w._tmr.isActive()
    w._apply_visibility(fullscreen=True)                       # игра на весь экран
    assert not w.isVisible() and not w._tmr.isActive()          # и не тратит кадры
    w._apply_visibility(fullscreen=False)
    assert w.isVisible()
    w.set_wanted(False)
    assert not w.isVisible()


def test_capsule_springs_to_size_and_mask_lets_clicks_through(island):
    w, app, _ = island
    w.set_wanted(True)
    _settle(w, app)
    cw, ch = ui.SIZES["compact"]
    assert abs(w._w - cw) < 1 and abs(w._h - ch) < 1
    mask = w.mask().boundingRect()
    assert mask.width() <= cw + 4 and mask.height() <= ch + 4  # вне капсулы окна нет
    assert mask.center().x() in range(w.W // 2 - 3, w.W // 2 + 4)   # по центру
    w.model.notify("ДЖАРВИС", "Готово, сэр.", "reply")
    _settle(w, app)
    assert abs(w._h - ui.SIZES["banner"][1]) < 1


def test_click_opens_jarvis_and_player_buttons_work(island, monkeypatch):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    w, app, opened = island
    sent = []
    monkeypatch.setattr(ui, "media_command", lambda media, action: sent.append((media.title, action)))
    w.set_wanted(True)
    _settle(w, app)
    c = w.capsule_rect().center()
    QTest.mouseClick(w, Qt.MouseButton.LeftButton, pos=QPoint(int(c.x()), int(c.y())))
    assert opened == [1]                                        # клик по капсуле — окно Джарвиса

    w.model.media = ui.Media("Macan — ASPHALT 8", "Macan", "music", True)
    w.hovered = True
    _settle(w, app, 1.4)
    w.repaint()
    assert set(w._buttons) == {"open", "previous", "toggle", "next"}
    for name in ("next", "toggle"):
        r = w._buttons[name].center()
        QTest.mouseClick(w, Qt.MouseButton.LeftButton, pos=QPoint(int(r.x()), int(r.y())))
    for _ in range(50):
        if len(sent) == 2:
            break
        time.sleep(0.01)
    assert sent == [("Macan — ASPHALT 8", "next"), ("Macan — ASPHALT 8", "toggle")]
    assert w.model.media.playing is False                       # кнопка сразу показывает паузу
    assert opened == [1]                                        # кнопки не разворачивают окно


def test_new_track_announced_briefly(island):
    w, app, _ = island
    w._set_media(ui.Media("Believer", "Imagine Dragons", "music", True), "", 0)
    b = w.model.banner()
    assert b and b.title == "ИГРАЕТ" and "Believer — Imagine Dragons" in b.text
    w.model.banners.clear()
    w._set_media(ui.Media("Believer", "Imagine Dragons", "music", True), "", 0)
    assert w.model.banner() is None                             # тот же трек — без повторов


def test_main_window_minimize_shows_island(monkeypatch):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui as jarvis_ui
    win = jarvis_ui.JarvisUI("face.png")
    try:
        app.processEvents()
        isl = win._island
        assert isl is not None and not isl.isVisible()
        win.showMinimized()
        app.processEvents()
        assert isl.wanted
        win.showNormal()
        app.processEvents()
        assert not isl.wanted
        win.write_log("Джарвис: Готово, сэр.")
        win.write_log("SYS: 📞 Вы не взяли трубку.")
        app.processEvents()
        assert isl.model.last_reply == "Готово, сэр."
        assert any(b.title == "ЗВОНОК" for b in isl.model.banners)
        win.set_state("LISTENING")
        app.processEvents()
        assert isl.model.state == "listening"
    finally:
        win._island.close()
        win.hide()
        win.deleteLater()
