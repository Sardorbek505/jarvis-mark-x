"""Браузер в интерфейсе рядом с шаром.

Ядро (core/browser_panel.py) — на настоящем Chrome/Chromium: кадры идут,
клик, текст, клавиши, колесо, размер и «назад» доходят до страницы, окно
отдаётся фильму. Виджет (ui_browser.py) — в Qt без экрана с подменённым
Chrome: клик по уменьшенной картинке попадает в ту же точку страницы.
И маршрут: «открой сайт» идёт в панель, а без неё — как раньше."""
import functools
import http.server
import os
import shutil
import threading
import time

import pytest

from core import browser_cdp as cdp
from core import browser_panel as bp

PAGE = """<!doctype html><meta charset="utf-8"><title>Проверка панели</title>
<body style="margin:0;height:3000px;font:20px sans-serif">
<button id="b" style="position:absolute;left:40px;top:40px;width:200px;height:60px"
        onclick="this.textContent='нажата'">кнопка</button>
<input id="i" style="position:absolute;left:40px;top:140px;width:300px;height:40px">
<a id="next" href="second.html" style="position:absolute;left:40px;top:220px">дальше</a>
<button id="fs" style="position:absolute;left:40px;top:300px;width:200px;height:50px"
        onclick="document.documentElement.requestFullscreen()">⛶ на весь экран</button>"""


def test_address_bar_understands_sites_and_queries():
    assert bp.normalize_url("youtube.com") == "https://youtube.com"
    assert bp.normalize_url("vk.com/video") == "https://vk.com/video"
    assert bp.normalize_url("https://a.b/c?d=1") == "https://a.b/c?d=1"
    assert bp.normalize_url("погода в Ташкенте").startswith("https://www.google.com/search?q=%D0%BF")
    assert bp.normalize_url("localhost:8080") .startswith("https://www.google.com/search")  # без точки — поиск
    assert bp.normalize_url("") == "about:blank"


def _chrome():
    for c in (os.getenv("JARVIS_BROWSER"), shutil.which("google-chrome"), shutil.which("chromium"),
              "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
        if c and os.path.isfile(c):
            return c
    return None


@pytest.fixture
def chrome(tmp_path, monkeypatch):
    if not _chrome():
        pytest.skip("нет Chrome/Chromium")
    (tmp_path / "site").mkdir()
    (tmp_path / "site" / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "site" / "second.html").write_text('<meta charset="utf-8"><title>Вторая</title>вторая',
                                                   encoding="utf-8")
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path / "site")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(cdp, "PORT", 9600 + os.getpid() % 90)
    monkeypatch.setattr(cdp, "_tab", None)
    monkeypatch.setattr(cdp, "_profile_dir", lambda: str(tmp_path / "profile"))
    monkeypatch.setattr(bp, "_panel", None)
    monkeypatch.setenv("JARVIS_BROWSER", _chrome())
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    monkeypatch.setenv("JARVIS_BROWSER_ARGS", "--headless=new --mute-audio" + (" --no-sandbox" if root else ""))
    try:
        assert cdp.ensure_browser(), cdp.browser_log()[-2000:]
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        if bp._panel:
            bp._panel.disconnect()
        try:
            if cdp._proc:
                cdp._proc.terminate()
        finally:
            srv.shutdown()


def _until(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def test_panel_streams_page_and_forwards_input(chrome):
    p = bp.panel()
    frames, urls, closed = [], [], []
    p.on_frame = lambda jpeg, meta: frames.append(jpeg)
    p.on_url = urls.append
    p.on_closed = lambda: closed.append(1)
    assert p.open(chrome + "/index.html", 640, 480, 1.0)
    assert _until(lambda: frames and frames[-1][:2] == b"\xff\xd8")          # JPEG пришёл
    t = cdp.tab()
    assert _until(lambda: t.eval("document.title") == "Проверка панели")
    assert t.eval("innerWidth + 'x' + innerHeight") == "640x480"          # страница под размер панели

    p.click(140, 70)                                                         # клик по кнопке
    assert _until(lambda: t.eval("b.textContent") == "нажата")
    p.click(100, 160)
    p.text("Привет, мир")
    p.key("Backspace")
    assert _until(lambda: t.eval("i.value") == "Привет, ми")
    p.wheel(100, 100, 0, 600)
    assert _until(lambda: t.eval("scrollY") == 600)

    p.resize(500, 400)
    assert _until(lambda: t.eval("innerWidth + 'x' + innerHeight") == "500x400")

    p.go(chrome + "/second.html")
    assert _until(lambda: t.eval("document.title") == "Вторая")
    assert urls and urls[-1].endswith("/second.html")
    p.back()
    assert _until(lambda: t.eval("document.title") == "Проверка панели")
    assert p.title() == "Проверка панели"

    # Кнопка полного экрана на самом сайте (как ⛶ в плеере YouTube): окно
    # должно выйти на экран во весь экран, а не развернуться за его краем.
    t.eval("scrollTo(0, 0)")                  # «назад» вернул страницу прокрученной
    assert _until(lambda: t.eval("scrollY") == 0)
    p.click(140, 325)
    assert _until(lambda: not p.active) and closed == [1]
    assert _until(lambda: t.eval("!!document.fullscreenElement"))
    wid = p._window()
    assert _until(lambda: p.call("Browser.getWindowBounds", windowId=wid)["bounds"]["windowState"] == "fullscreen")
    t.eval("document.exitFullscreen()")

    assert p.open(None, 500, 400, 1.0) and p.active                           # снова в панель
    bp.release_for_video()                                                  # фильм забирает окно
    assert not p.active and closed == [1, 1]
    assert _until(lambda: t.eval("innerWidth") != 500)                      # разметка сброшена
    n = p.frames
    time.sleep(0.4)
    assert p.frames - n <= 1                                                # трансляция стоит


# ── виджет ───────────────────────────────────────────────────────────────────

class FakePanel:
    def __init__(self):
        self._size = (800, 600, 1.0)
        self.calls = []
        self.on_frame = self.on_url = self.on_closed = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self.calls.append((name, a, k))


@pytest.fixture
def view():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui_browser import BrowserPanelView
    fake = FakePanel()
    v = BrowserPanelView(panel=fake)
    shown = []
    v.on_visibility = shown.append
    v.resize(456, 400)
    v.reveal("https://example.com/")
    app.processEvents()
    yield v, fake, shown, app
    v.deleteLater()


def _jpeg(w, h):
    from PyQt6.QtCore import QBuffer, QByteArray
    from PyQt6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#123456"))
    buf = QBuffer(ba := QByteArray())
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "JPG")
    return bytes(ba)


def _wait_calls(fake, app, name, n=1):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        app.processEvents()
        got = [c for c in fake.calls if c[0] == name]
        if len(got) >= n:
            return got
        time.sleep(0.01)
    return [c for c in fake.calls if c[0] == name]


def test_click_on_scaled_picture_hits_same_page_point(view):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    v, fake, shown, app = view
    assert shown == [True] and v.address.text() == "https://example.com/"
    v.frame_sig.emit(_jpeg(800, 600))                      # страница 800×600
    app.processEvents()
    v.view.repaint()
    r = v.view.draw_rect
    assert r.width() < 800 and abs(r.width() / r.height() - 800 / 600) < 0.01   # вписана, пропорции те же
    # центр картинки → центр страницы, правый нижний угол → (почти) 800×600
    QTest.mouseClick(v.view, Qt.MouseButton.LeftButton, pos=QPoint(int(r.center().x()), int(r.center().y())))
    press = _wait_calls(fake, app, "mouse")[0]
    assert press[1][0] == "press"
    x, y = press[1][1], press[1][2]
    assert abs(x - 400) < 4 and abs(y - 300) < 4
    fake.calls.clear()
    QTest.mouseClick(v.view, Qt.MouseButton.LeftButton, pos=QPoint(int(r.right() - 1), int(r.bottom() - 1)))
    x, y = _wait_calls(fake, app, "mouse")[0][1][1:3]
    assert x > 790 and y > 590
    fake.calls.clear()
    QTest.mouseClick(v.view, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))  # мимо картинки (поле) — ничего
    app.processEvents()
    time.sleep(0.05)
    assert not [c for c in fake.calls if c[0] == "mouse" and c[1][1] < 0]


def test_keyboard_wheel_address_and_buttons(view):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtTest import QTest
    v, fake, shown, app = view
    v.frame_sig.emit(_jpeg(800, 600))
    app.processEvents()
    v.view.repaint()
    v.view.setFocus()
    QTest.keyClicks(v.view, "ok")
    QTest.keyClick(v.view, Qt.Key.Key_Return)
    QTest.keyClick(v.view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert [c[1][0] for c in _wait_calls(fake, app, "text", 2)] == ["o", "k"]
    keys = _wait_calls(fake, app, "key", 2)
    assert keys[0][1] == ("Enter", 0) and keys[1][1] == ("v", 2)

    c = v.view.draw_rect.center()
    ev = QWheelEvent(QPointF(c), QPointF(v.view.mapToGlobal(QPoint(int(c.x()), int(c.y())))), QPoint(),
                     QPoint(0, -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(v.view, ev)
    wheel = _wait_calls(fake, app, "wheel")[0][1]
    assert wheel[3] == 100                                   # колесо вниз → страница вниз на 100

    v.address.setText("погода")
    QTest.keyClick(v.address, Qt.Key.Key_Return)
    assert _wait_calls(fake, app, "go")[0][1] == ("погода",)

    v.panel.on_url("https://ya.ru/")                          # страница сменилась сама
    app.processEvents()
    assert v.address.text() == "https://ya.ru/"

    v.expand()
    assert not v.isVisible() and shown[-1] is False
    assert _wait_calls(fake, app, "expand")


def test_panel_closed_from_chrome_side_hides_widget(view):
    v, fake, shown, app = view
    v.panel.on_closed()                                       # фильм забрал окно (из другого потока)
    app.processEvents()
    assert not v.isVisible() and shown[-1] is False


# ── маршрут команд ───────────────────────────────────────────────────────────

class Player:
    def __init__(self, ok=True):
        self.ok, self.opened, self.logs = ok, [], []

    def open_in_panel(self, url):
        self.opened.append(url)
        return self.ok

    def write_log(self, s):
        self.logs.append(s)


def test_open_site_goes_to_panel_and_falls_back(monkeypatch):
    import actions.browser_control as bc
    outside = []
    monkeypatch.setattr(bc, "_open_url", lambda url, browser=None: outside.append((url, browser)) or True)
    pl = Player()
    assert bc.browser_control({"action": "go_to", "url": "youtube.com"}, player=pl) == "Открыл https://youtube.com."
    assert pl.opened == ["https://youtube.com"] and outside == []
    bc.browser_control({"action": "search", "query": "курс доллара"}, player=pl)
    assert "google.com/search?q=" in pl.opened[-1] and outside == []

    broken = Player(ok=False)                                  # панель не поднялась — окном, как раньше
    bc.browser_control({"action": "go_to", "url": "vk.com"}, player=broken)
    assert outside == [("https://vk.com", None)]
    bc.browser_control({"action": "go_to", "url": "vk.com", "browser": "edge"}, player=pl)
    assert outside[-1] == ("https://vk.com", "edge")           # попросили конкретный браузер
    monkeypatch.setenv("JARVIS_BROWSER_PANEL", "0")
    bc.browser_control({"action": "go_to", "url": "mail.ru"}, player=pl)
    assert outside[-1] == ("https://mail.ru", None) and "https://mail.ru" not in pl.opened


def test_main_window_starts_and_opens_panel(monkeypatch):
    """Настоящее окно Джарвиса: запускается (раньше падало — расстановка
    панелей шла до создания браузера), открывает панель и сдвигает шар."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    opened = []

    class Stub:
        _size = (800, 600, 1.0)
        on_frame = on_url = on_closed = None

        def open(self, url, w, h, d):
            opened.append((url, w, h))
            return True

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(bp, "_panel", Stub())
    import ui
    win = ui.JarvisUI("face.png")                 # тот же класс, что запускает main.py
    try:
        win.resize(1280, 760)
        win.show()
        app.processEvents()
        assert win.open_in_panel("https://example.com")
        for _ in range(20):
            app.processEvents()
            time.sleep(0.01)
        assert opened and opened[0][0] == "https://example.com" and opened[0][1] > 300
        assert win._browser.isVisible()
        assert win._hud.side_px > 300                                      # шар уступил место
        win._browser.close_panel()
        app.processEvents()
        assert not win._browser.isVisible() and win._hud.side_px == 0
    finally:
        if win._island is not None:
            win._island.close()                     # фоновый опрос капсулы не должен пережить тест
        win.hide()
        win.deleteLater()


def test_tab_is_safe_from_many_threads(chrome):
    """Голос, капсула и панель зовут одну вкладку из разных потоков. Раньше
    это падало ConcurrencyError: два recv на одном соединении."""
    t = cdp.tab()
    errors, results = [], []

    def worker(k):
        try:
            for i in range(15):
                results.append(t.eval(f"{k} * 100 + {i}") == k * 100 + i)
        except Exception as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    assert errors == [] and len(results) == 120 and all(results)
