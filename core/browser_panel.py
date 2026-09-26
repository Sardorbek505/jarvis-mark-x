"""Браузер в интерфейсе Джарвиса — трансляция вкладки Chrome в панель рядом с шаром.

Встраивать движок браузера в программу — плюс 100–150 МБ к установщику,
отдельные входы на все сайты и тормозящее видео. Вместо этого панель
показывает ту же вкладку Chrome Джарвиса (core/browser_cdp.py), в которой
играют фильмы: входы общие, установщик не растёт.

Как это устроено:
  • своё подключение к вкладке по DevTools (у browser_cdp.Tab события
    пропускаются, а здесь они нужны) и поток, который их читает;
  • Page.startScreencast — Chrome сам присылает кадры (JPEG), когда
    страница меняется; каждый кадр подтверждаем, иначе поток встаёт;
  • страница размечается под размер панели (Emulation.setDeviceMetricsOverride),
    а само окно Chrome уезжает за край экрана. Свернуть нельзя — свёрнутое
    окно не рисуется; флаги против «засыпания» невидимого окна — в browser_cdp;
  • клики, колесо и текст уходят в страницу через Input.*;
  • «На весь экран» и фильм забирают окно у панели: разметка сбрасывается,
    окно возвращается на экран.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections.abc import Callable

from core import browser_cdp as cdp

logger = logging.getLogger(__name__)

# Клавиши, которые не набираются текстом: имя → (key, code, keyCode).
_KEYS = {
    "Enter": ("Enter", "Enter", 13), "Backspace": ("Backspace", "Backspace", 8),
    "Tab": ("Tab", "Tab", 9), "Escape": ("Escape", "Escape", 27), "Delete": ("Delete", "Delete", 46),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37), "ArrowUp": ("ArrowUp", "ArrowUp", 38),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39), "ArrowDown": ("ArrowDown", "ArrowDown", 40),
    "Home": ("Home", "Home", 36), "End": ("End", "End", 35),
    "PageUp": ("PageUp", "PageUp", 33), "PageDown": ("PageDown", "PageDown", 34),
}
_MODS = {"alt": 1, "ctrl": 2, "meta": 4, "shift": 8}

# Полный экран, запрошенный самой страницей (кнопка ⛶ в плеере YouTube, двойной
# клик по видео), случился бы в окне за краем экрана — смотреть было бы нечего.
# Страница сообщает об этом через binding, и Джарвис выводит окно на экран.
_FS_BINDING = "__jarvisFullscreen"
_FS_HOOK = ("(() => { if (window.__jarvisFsHook) return; window.__jarvisFsHook = true;"
            " const on = () => { const el = document.fullscreenElement || document.webkitFullscreenElement;"
            f" if (el) {{ window.__jarvisFsEl = el; window.{_FS_BINDING} && window.{_FS_BINDING}('on'); }} }};"
            " document.addEventListener('fullscreenchange', on, true);"
            " document.addEventListener('webkitfullscreenchange', on, true); })()")


def normalize_url(text: str) -> str:
    """Что ввели в адресную строку → адрес: сайт или поиск Google."""
    import re
    import urllib.parse
    t = (text or "").strip()
    if not t:
        return "about:blank"
    if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I) or t.startswith("about:"):
        return t
    if " " not in t and re.match(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/.*)?$", t):
        return "https://" + t
    return "https://www.google.com/search?q=" + urllib.parse.quote(t)


class Panel:
    """Одна трансляция. Колбэки зовутся из потока чтения — в Qt только сигналом."""

    def __init__(self):
        self.on_frame: Callable[[bytes, dict], None] = lambda jpeg, meta: None
        self.on_url: Callable[[str], None] = lambda url: None
        self.on_closed: Callable[[], None] = lambda: None
        self._ws = None
        self._reader: threading.Thread | None = None
        self._id = 0
        self._send_lock = threading.Lock()
        self._pending: dict[int, list] = {}
        self._window_id: int | None = None
        self._size = (900, 640, 1.0)
        self.active = False
        self._hooked = False
        self.url = ""
        self.frames = 0

    # ── связь ────────────────────────────────────────────────────────────────
    def _connect(self) -> bool:
        from websockets.sync.client import connect
        t = cdp.tab()
        if not t:
            return False
        pages = [p for p in cdp._http("/json/list") if p.get("id") == t.target_id]
        if not pages:
            return False
        self._ws = connect(pages[0]["webSocketDebuggerUrl"], max_size=None, open_timeout=5)
        self._reader = threading.Thread(target=self._read, daemon=True, name="browser-panel")
        self._reader.start()
        return True

    def _read(self):
        ws = self._ws
        while ws is self._ws and ws is not None:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                break
            if "id" in msg:
                slot = self._pending.pop(msg["id"], None)
                if slot:
                    slot[1] = msg
                    slot[0].set()
                continue
            method, params = msg.get("method"), msg.get("params", {})
            if method == "Page.screencastFrame":
                # Подтверждаем сразу: без ответа Chrome следующий кадр не пришлёт.
                self._send("Page.screencastFrameAck", sessionId=params.get("sessionId"))
                self.frames += 1
                try:
                    self.on_frame(base64.b64decode(params.get("data", "")), params.get("metadata", {}))
                except Exception as exc:
                    logger.debug("Кадр панели: %s", exc)
            elif method == "Runtime.bindingCalled" and params.get("name") == _FS_BINDING:
                if self.active:
                    # Из потока чтения call() звать нельзя — он сам ждёт этот поток.
                    threading.Thread(target=self.fullscreen, daemon=True, name="panel-fs").start()
            elif method == "Page.frameNavigated" and not params.get("frame", {}).get("parentId"):
                self.url = params["frame"].get("url", "")
                self.on_url(self.url)
        if self.active:                     # связь оборвалась сама — панель закрывается
            self.active = False
            self.on_closed()

    def _send(self, method: str, **params) -> int:
        with self._send_lock:
            self._id += 1
            mid = self._id
            self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        return mid

    def call(self, method: str, timeout: float = 8.0, **params) -> dict:
        """Команда с ответом. Не из потока чтения — он и приносит ответ."""
        ev = threading.Event()
        slot = [ev, None]
        with self._send_lock:
            self._id += 1
            mid = self._id
            self._pending[mid] = slot
            self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        if not ev.wait(timeout):
            self._pending.pop(mid, None)
            raise TimeoutError(f"CDP {method}: нет ответа")
        msg = slot[1]
        if "error" in msg:
            raise RuntimeError(f"CDP {method}: {msg['error'].get('message')}")
        return msg.get("result", {})

    def fire(self, method: str, **params):
        """Команда без ожидания — ввод мыши и клавиш не должен тормозить панель."""
        if self._ws is not None:
            try:
                self._send(method, **params)
            except Exception as exc:
                logger.debug("Панель %s: %s", method, exc)

    # ── жизнь панели ─────────────────────────────────────────────────────────
    def open(self, url: str | None = None, width: int | None = None, height: int | None = None,
             dpr: float | None = None) -> bool:
        """Показать вкладку в панели (и открыть url). True — трансляция идёт."""
        if width:
            self._size = (int(width), int(height or self._size[1]), float(dpr or self._size[2]))
        if self._ws is None and not self._connect():
            return False
        self.call("Page.enable")
        if not self._hooked:
            self.call("Runtime.enable")
            self.call("Runtime.addBinding", name=_FS_BINDING)
            self.call("Page.addScriptToEvaluateOnNewDocument", source=_FS_HOOK)
            self._hooked = True
        try:
            self.call("Runtime.evaluate", expression=_FS_HOOK)       # и в уже открытую страницу
        except Exception as exc:
            logger.debug("Хук полного экрана: %s", exc)
        self._apply_size()
        self._hide_window()
        if not self.active:
            w, h, d = self._size
            self.call("Page.startScreencast", format="jpeg", quality=72,
                      maxWidth=int(w * d), maxHeight=int(h * d), everyNthFrame=1)
            self.active = True
        if url:
            self.go(url)
        return True

    def resize(self, width: int, height: int, dpr: float = 1.0):
        self._size = (max(200, int(width)), max(150, int(height)), float(dpr))
        if self.active:
            self._apply_size()
            w, h, d = self._size
            self.fire("Page.stopScreencast")
            self.fire("Page.startScreencast", format="jpeg", quality=72,
                      maxWidth=int(w * d), maxHeight=int(h * d), everyNthFrame=1)

    def _apply_size(self):
        w, h, d = self._size
        self.call("Emulation.setDeviceMetricsOverride", width=w, height=h,
                  deviceScaleFactor=d, mobile=False)

    def _window(self) -> int | None:
        if self._window_id is None:
            try:
                self._window_id = self.call("Browser.getWindowForTarget").get("windowId")
            except Exception as exc:
                logger.debug("Окно панели: %s", exc)
        return self._window_id

    def _hide_window(self):
        """Окно Chrome — за край экрана: рисоваться продолжает, глаза не мозолит."""
        wid = self._window()
        if wid is None:
            return
        try:
            self.call("Browser.setWindowBounds", windowId=wid, bounds={"windowState": "normal"})
            w, h, _ = self._size
            self.call("Browser.setWindowBounds", windowId=wid,
                      bounds={"left": -32000, "top": 0, "width": w + 16, "height": h + 90})
        except Exception as exc:
            logger.debug("Спрятать окно: %s", exc)

    def release(self, maximize: bool = True):
        """Отдать окно: фильм или «на весь экран». Трансляция останавливается."""
        was = self.active
        self.active = False
        if self._ws is None:
            return
        for method, params in (("Page.stopScreencast", {}), ("Emulation.clearDeviceMetricsOverride", {})):
            try:
                self.call(method, timeout=3, **params)
            except Exception as exc:
                logger.debug("%s: %s", method, exc)
        wid = self._window()
        if wid is not None:
            try:
                self.call("Browser.setWindowBounds", windowId=wid,
                          bounds={"left": 60, "top": 40, "width": 1280, "height": 800})
                if maximize:
                    self.call("Browser.setWindowBounds", windowId=wid, bounds={"windowState": "maximized"})
            except Exception as exc:
                logger.debug("Вернуть окно: %s", exc)
        if was:
            self.on_closed()

    def fullscreen(self):
        """Страница ушла в полный экран — настоящее окно на экран, во весь экран.
        Видео дальше идёт в Chrome напрямую, в полном качестве.

        Переезд окна сбрасывает полный экран элемента (плеер сворачивался бы
        обратно в страницу), поэтому тот же элемент разворачиваем снова —
        как по клику пользователя (userGesture), иначе браузер не даст."""
        self.release(maximize=False)
        wid = self._window()
        if wid is not None:
            try:
                self.call("Browser.setWindowBounds", windowId=wid, bounds={"windowState": "fullscreen"})
            except Exception as exc:
                logger.debug("Полный экран окна: %s", exc)
        cdp.bring_to_front()
        # Переход окна в полный экран идёт асинхронно и может сбросить полный
        # экран элемента уже после удачной попытки — считаем готовым, только
        # когда элемент держится два замера подряд. До 6 с: медленный ПК.
        stable, end = 0, time.monotonic() + 6.0
        while time.monotonic() < end and stable < 2:
            try:
                r = self.call("Runtime.evaluate", userGesture=True, awaitPromise=True, returnByValue=True,
                              expression="(async () => { const el = window.__jarvisFsEl;"
                                         " if (!el || !el.isConnected) return 'none';"
                                         " if (document.fullscreenElement !== el) {"
                                         "   try { await el.requestFullscreen(); } catch (e) { return false; } }"
                                         " return document.fullscreenElement === el; })()")
                v = r.get("result", {}).get("value")
                if v == "none":
                    return
                stable = stable + 1 if v else 0
            except Exception as exc:
                stable = 0
                logger.debug("Полный экран элемента: %s", exc)
            time.sleep(0.25)

    def expand(self):
        """«На весь экран»: настоящее окно Chrome — вперёд, развёрнутым."""
        self.release(maximize=True)
        cdp.bring_to_front()

    def close(self):
        """Спрятать панель. Вкладка остаётся — ею пользуются фильмы."""
        self.release(maximize=False)
        wid = self._window()
        if wid is not None:
            try:
                self.call("Browser.setWindowBounds", windowId=wid, bounds={"windowState": "minimized"})
            except Exception as exc:
                logger.debug("Свернуть окно: %s", exc)

    def disconnect(self):
        ws, self._ws = self._ws, None
        self.active = False
        self._hooked = False
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    # ── навигация ────────────────────────────────────────────────────────────
    def go(self, text: str):
        url = normalize_url(text)
        self.url = url
        self.call("Page.navigate", url=url)

    def history(self, step: int):
        h = self.call("Page.getNavigationHistory")
        i = h.get("currentIndex", 0) + step
        entries = h.get("entries", [])
        if 0 <= i < len(entries):
            self.call("Page.navigateToHistoryEntry", entryId=entries[i]["id"])

    def back(self):
        self.history(-1)

    def forward(self):
        self.history(1)

    def reload(self):
        self.call("Page.reload")

    def title(self) -> str:
        try:
            r = self.call("Runtime.evaluate", expression="document.title", returnByValue=True, timeout=3)
            return r.get("result", {}).get("value") or ""
        except Exception:
            return ""

    # ── ввод ─────────────────────────────────────────────────────────────────
    def mouse(self, kind: str, x: float, y: float, button: str = "left", clicks: int = 1, mods: int = 0):
        """kind: press / release / move. Координаты — в точках страницы."""
        t = {"press": "mousePressed", "release": "mouseReleased", "move": "mouseMoved"}[kind]
        self.fire("Input.dispatchMouseEvent", type=t, x=x, y=y, modifiers=mods,
                  button=button if kind != "move" else "none", clickCount=clicks if kind != "move" else 0)

    def click(self, x: float, y: float):
        self.mouse("press", x, y)
        self.mouse("release", x, y)

    def wheel(self, x: float, y: float, dx: float, dy: float):
        self.fire("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=dx, deltaY=dy)

    def text(self, s: str):
        if s:
            self.fire("Input.insertText", text=s)

    def key(self, name: str, mods: int = 0):
        """Особая клавиша (Enter, Backspace, стрелки…) или сочетание с Ctrl."""
        if name in _KEYS:
            key, code, vk = _KEYS[name]
        elif len(name) == 1:
            key, code, vk = name, "Key" + name.upper(), ord(name.upper())
        else:
            return
        down = {"type": "rawKeyDown", "key": key, "code": code, "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk, "modifiers": mods}
        self.fire("Input.dispatchKeyEvent", **down)
        if name == "Enter" and not mods:
            self.fire("Input.dispatchKeyEvent", type="char", text="\r", key="Enter", modifiers=mods)
        self.fire("Input.dispatchKeyEvent", **{**down, "type": "keyUp"})


_panel: Panel | None = None
_lock = threading.Lock()


def panel() -> Panel:
    global _panel
    with _lock:
        if _panel is None:
            _panel = Panel()
        return _panel


def active() -> bool:
    return _panel is not None and _panel.active


def release_for_video():
    """Фильм или ролик забирает окно: панель закрывается, окно — на экран."""
    if _panel is not None and _panel.active:
        _panel.release(maximize=True)
        time.sleep(0.1)
