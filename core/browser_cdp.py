"""Своё окно браузера Джарвиса и управление видео в нём (Chrome DevTools Protocol).

Раньше фильм «управлялся» нажатием клавиш в то, что впереди: Space
прокручивал страницу, стрелки листали, пауза жала не туда, а спросить
«сколько осталось» было не у кого. Теперь Джарвис открывает Chrome (нет —
Edge) со своим профилем и портом отладки и говорит с плеером страницы
напрямую: play/pause, перемотка на точное время, громкость, mute, полный
экран, текущее время и длительность — и сразу видит, получилось ли.

Профиль отдельный (%APPDATA%\\JARVIS\\browser): Chrome не разрешает
управление основным профилем. В VK в этом окне достаточно войти один раз.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)

PORT = int(os.getenv("JARVIS_CDP_PORT", "9229"))
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_lock = threading.RLock()
_tab: "Tab | None" = None
_proc: subprocess.Popen | None = None


# ── запуск браузера ───────────────────────────────────────────────────────────

# К своему же браузеру — всегда напрямую: если в системе прописан прокси,
# urllib отправил бы и 127.0.0.1 через него, и порт «не отвечал» бы.
_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(path: str, method: str = "GET", timeout: float = 2.0):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with _direct.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def running() -> bool:
    try:
        return bool(_http("/json/version", timeout=0.5))
    except Exception:
        return False


def browser_exe() -> str | None:
    env = os.getenv("JARVIS_BROWSER", "").strip()
    if env and os.path.isfile(env):
        return env
    if sys.platform == "win32":
        from actions.browser_control import _browser_exe
        return _browser_exe("chrome") or _browser_exe("edge")
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _profile_dir() -> str:
    try:
        from core.paths import get_data_root
        d = os.path.join(str(get_data_root()), "browser")
    except Exception:
        d = os.path.join(os.path.expanduser("~"), ".jarvis-browser")
    os.makedirs(d, exist_ok=True)
    return d


def ensure_browser(headless: bool = False, timeout: float = 12.0) -> bool:
    """Поднять окно Джарвиса, если его нет. True — порт отвечает."""
    global _proc
    if running():
        return True
    exe = browser_exe()
    if not exe:
        logger.warning("Не нашёл Chrome/Edge для окна Джарвиса")
        return False
    args = [exe, f"--remote-debugging-port={PORT}", f"--user-data-dir={_profile_dir()}",
            "--no-first-run", "--no-default-browser-check",
            # Видео запускается само и со звуком — без клика по странице.
            "--autoplay-policy=no-user-gesture-required",
            "--start-maximized", "--disable-features=Translate,MediaRouter", "about:blank"]
    if headless:
        args[1:1] = ["--headless=new", "--mute-audio"]
    extra = os.getenv("JARVIS_BROWSER_ARGS", "").split()
    if extra:
        args[1:1] = extra
    _proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=_NO_WINDOW)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if running():
            return True
        time.sleep(0.15)
    return False


# ── вкладка ───────────────────────────────────────────────────────────────────

class Tab:
    """Одно соединение с вкладкой: команды по id, события пропускаем."""

    def __init__(self, ws_url: str, target_id: str):
        from websockets.sync.client import connect
        self.target_id = target_id
        self._ws = connect(ws_url, max_size=None, open_timeout=5)
        self._id = 0

    def call(self, method: str, timeout: float = 10.0, **params):
        self._id += 1
        my = self._id
        self._ws.send(json.dumps({"id": my, "method": method, "params": params}))
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"CDP {method}: нет ответа")
            msg = json.loads(self._ws.recv(timeout=left))
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def eval(self, js: str, await_promise: bool = False, timeout: float = 10.0):
        """Выполнить JS как будто по клику пользователя (userGesture): иначе
        браузер не даёт ни полный экран, ни звук."""
        r = self.call("Runtime.evaluate", timeout=timeout, expression=js, returnByValue=True,
                      awaitPromise=await_promise, userGesture=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"].get("text", "JS error"))
        return r.get("result", {}).get("value")

    def navigate(self, url: str):
        self.call("Page.navigate", url=url)

    def wait(self, js: str, timeout: float = 10.0, interval: float = 0.2):
        """Ждать, пока выражение вернёт что-то истинное. Возвращает значение."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                v = self.eval(js, timeout=3)
                if v:
                    return v
            except Exception:
                pass                      # страница ещё грузится
            time.sleep(interval)
        return None

    def url(self) -> str:
        return self.eval("location.href") or ""

    def alive(self) -> bool:
        try:
            self.eval("1", timeout=2)
            return True
        except Exception:
            return False

    def close(self):
        try:
            self._ws.close()
        except Exception:
            pass


def tab(create: bool = True) -> Tab | None:
    """Вкладка Джарвиса (одна на всё: фильм, ролик). Поднимает браузер."""
    global _tab
    with _lock:
        if _tab and _tab.alive():
            return _tab
        if _tab:
            _tab.close()
            _tab = None
        if not running():
            if not create or not ensure_browser():
                return None
        pages = [t for t in _http("/json/list") if t.get("type") == "page"]
        if not pages:
            if not create:
                return None
            pages = [_http("/json/new?about:blank", method="PUT")]
        page = pages[0]
        _tab = Tab(page["webSocketDebuggerUrl"], page["id"])
        return _tab


def bring_to_front():
    """Окно браузера — вперёд и поверх Джарвиса."""
    t = tab(create=False)
    if not t:
        return
    try:
        t.call("Page.bringToFront")
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            from core import win_apps
            title = (t.eval("document.title") or "").strip()
            wins = [w for w in win_apps.list_windows()
                    if w.exe in ("chrome.exe", "msedge.exe") and title and title[:30] in w.title]
            if wins:
                win_apps.focus(wins[0])
        except Exception as exc:
            logger.debug("Окно браузера вперёд: %s", exc)


# ── видео на странице ─────────────────────────────────────────────────────────

# Главное видео страницы — самое большое по площади (рядом бывают превью).
_VIDEO = ("(() => { const vs = [...document.querySelectorAll('video')]"
          ".filter(v => v.getBoundingClientRect().width > 50);"
          " vs.sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight);"
          " return vs[0] || null; })()")

_STATE = ("(v => v ? {paused: v.paused, t: v.currentTime, d: v.duration || 0,"
          " vol: v.volume, muted: v.muted, fs: !!document.fullscreenElement,"
          " ready: v.readyState} : null)")


def video_state(t: Tab | None = None) -> dict | None:
    t = t or tab(create=False)
    if not t:
        return None
    try:
        return t.eval(f"{_STATE}({_VIDEO})")
    except Exception:
        return None


def video_js(body: str, t: Tab | None = None, await_promise: bool = False):
    """Выполнить действие над главным видео: в body доступна переменная v.
    Возвращает состояние после действия или None, если видео нет."""
    t = t or tab(create=False)
    if not t:
        return None
    js = (f"(async () => {{ const v = {_VIDEO}; if (!v) return null; {body};"
          f" return {_STATE}(v); }})()")
    return t.eval(js, await_promise=True)


def fullscreen(on: bool = True, t: Tab | None = None) -> bool:
    """Полный экран плеера (с его кнопками), а не голого <video>."""
    t = t or tab(create=False)
    if not t:
        return False
    if on:
        js = ("(async () => { const v = " + _VIDEO + "; if (!v) return false;"
              " if (document.fullscreenElement) return true;"
              " const box = v.closest('.html5-video-player, [class*=\"player\" i], [class*=\"Player\"]') || v;"
              " try { await box.requestFullscreen(); } catch (e) { await v.requestFullscreen(); }"
              " return !!document.fullscreenElement; })()")
    else:
        js = ("(async () => { if (document.fullscreenElement) await document.exitFullscreen();"
              " return !document.fullscreenElement; })()")
    try:
        return bool(t.eval(js, await_promise=True))
    except Exception as exc:
        logger.debug("Полный экран: %s", exc)
        return False
