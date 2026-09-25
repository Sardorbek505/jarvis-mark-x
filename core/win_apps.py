"""Программы и окна Windows: найти, запустить, переключиться, свернуть, закрыть.

Раньше программы искались только в PATH — Telegram, Discord, Steam, Spotify
там не лежат, и «открой телеграм» не работало никогда. Окна искались по
заголовку («хром» не находил «Google Chrome», у Spotify в заголовке название
песни), а «закрой хром» жал Alt+F4 тому, что впереди, — хоть самому Джарвису.

Теперь:
- список программ берётся из меню «Пуск» (Get-StartApps — там и обычные,
  и программы из Microsoft Store) плюс ярлыки .lnk и известные пути;
- имя понимается по-русски («телега», «хром», «ворд») и с ошибками (rapidfuzz);
- окно ищется по процессу (chrome.exe), а не по заголовку;
- действия над окном — прямыми вызовами Win32 (ShowWindow, WM_CLOSE),
  своё окно Джарвиса не трогается никогда.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_WIN = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if _WIN else 0

# Русские и разговорные имена → каноническое имя программы.
ALIASES = {
    "хром": "chrome", "гугл хром": "chrome", "google chrome": "chrome", "гугл": "chrome",
    "телеграм": "telegram", "телеграмм": "telegram", "телега": "telegram", "тг": "telegram",
    "telegram desktop": "telegram",
    "дискорд": "discord", "спотифай": "spotify", "спотик": "spotify",
    "стим": "steam", "ворд": "word", "эксель": "excel", "поверпоинт": "powerpoint",
    "павер поинт": "powerpoint", "зум": "zoom", "фотошоп": "photoshop", "фигма": "figma",
    "блокнот": "notepad", "калькулятор": "calculator", "яндекс": "yandex",
    "яндекс браузер": "yandex", "вотсап": "whatsapp", "ватсап": "whatsapp",
    "вс код": "visual studio code", "вскод": "visual studio code", "vs code": "visual studio code",
    "vscode": "visual studio code", "визуал студио код": "visual studio code", "код": "visual studio code",
    "обс": "obs", "эдж": "edge", "едж": "edge", "фаерфокс": "firefox", "мозилла": "firefox",
    "опера": "opera", "вк": "vk", "скайп": "skype", "тимс": "teams", "микрософт тимс": "teams",
    "проводник": "explorer", "диспетчер задач": "task manager", "паинт": "paint",
    "пейнт": "paint", "терминал": "terminal", "консоль": "terminal", "виндовс терминал": "terminal",
    "настройки": "settings", "параметры": "settings", "плеер": "vlc", "влс": "vlc",
}

# Каноническое имя → exe процесса (для поиска окна и «уже запущено»).
PROCESS = {
    "chrome": "chrome.exe", "telegram": "telegram.exe", "spotify": "spotify.exe",
    "discord": "discord.exe", "steam": "steam.exe", "edge": "msedge.exe",
    "firefox": "firefox.exe", "yandex": "browser.exe", "opera": "opera.exe",
    "visual studio code": "code.exe", "word": "winword.exe", "excel": "excel.exe",
    "powerpoint": "powerpnt.exe", "notepad": "notepad.exe", "vlc": "vlc.exe",
    "zoom": "zoom.exe", "obs": "obs64.exe", "whatsapp": "whatsapp.exe",
    "skype": "skype.exe", "teams": "ms-teams.exe", "figma": "figma.exe",
    "photoshop": "photoshop.exe", "paint": "mspaint.exe", "calculator": "calculatorapp.exe",
}

# Встроенное в Windows — запуск без поиска.
BUILTIN = {
    "explorer": ["explorer.exe"], "task manager": ["taskmgr.exe"],
    "settings": ["ms-settings:"], "notepad": ["notepad.exe"], "calculator": ["calc.exe"],
    "paint": ["mspaint.exe"], "terminal": ["wt.exe", "powershell.exe"],
    "cmd": ["cmd.exe"], "powershell": ["powershell.exe"],
}

# Программы, которых часто нет в «Пуске» (ставятся в профиль пользователя).
_KNOWN_PATHS = {
    "spotify": [r"%APPDATA%\Spotify\Spotify.exe"],
    "telegram": [r"%APPDATA%\Telegram Desktop\Telegram.exe"],
    "discord": [r"%LOCALAPPDATA%\Discord\Update.exe"],
    "yandex": [r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"],
    "chrome": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
               r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
    "visual studio code": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"],
}

_JUNK = re.compile(r"uninstall|удал|help|справк|readme|documentation|документац|release notes|"
                   r"website|веб-сайт|support|поддержк|manual|license|лиценз", re.I)


def canonical(name: str) -> str:
    """«Телега» → «telegram», «Google Chrome» → «chrome»."""
    n = re.sub(r"\s+", " ", (name or "").strip().lower().replace("ё", "е"))
    n = re.sub(r"^(программ[ауы]|приложени[ея]|браузер)\s+", "", n)
    return ALIASES.get(n, n)


# ── индекс программ ───────────────────────────────────────────────────────────

@dataclass
class App:
    name: str
    appid: str = ""        # из Get-StartApps: запуск через shell:AppsFolder
    path: str = ""         # ярлык .lnk или .exe


_index: list[App] = []
_index_at = 0.0
_index_lock = threading.Lock()


def _start_apps() -> list[App]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
        capture_output=True, timeout=25, creationflags=_NO_WINDOW)
    data = json.loads(out.stdout.decode("utf-8", "replace") or "[]")
    if isinstance(data, dict):
        data = [data]
    return [App(d.get("Name", ""), appid=d.get("AppID", "")) for d in data if d.get("Name")]


def _shortcuts() -> list[App]:
    roots = [os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
             os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
             os.path.join(os.path.expanduser("~"), "Desktop")]
    apps = []
    for root in roots:
        for lnk in Path(root).rglob("*.lnk") if os.path.isdir(root) else []:
            apps.append(App(lnk.stem, path=str(lnk)))
    return apps


def _known() -> list[App]:
    apps = []
    for name, paths in _KNOWN_PATHS.items():
        for p in paths:
            full = os.path.expandvars(p)
            if os.path.isfile(full):
                apps.append(App(name, path=full))
                break
    return apps


def build_index(force: bool = False) -> list[App]:
    """Собрать список программ (на Windows ~1-3 с, потом кэш на 10 минут)."""
    global _index, _index_at
    with _index_lock:
        if _index and not force and time.monotonic() - _index_at < 600:
            return _index
        apps: list[App] = []
        if _WIN:
            for source in (_start_apps, _shortcuts, _known):
                try:
                    apps += source()
                except Exception as exc:
                    logger.debug("Индекс программ (%s): %s", source.__name__, exc)
        _index = [a for a in apps if a.name and not _JUNK.search(a.name)]
        _index_at = time.monotonic()
        logger.info("Программ в индексе: %d", len(_index))
        return _index


def warm_up():
    """Собрать индекс в фоне при старте — первое «открой…» не ждёт."""
    threading.Thread(target=build_index, daemon=True, name="app-index").start()


def find_app(query: str, apps: list[App] | None = None) -> App | None:
    key = canonical(query)
    apps = build_index() if apps is None else apps
    if not key or not apps:
        return None

    def norm(s: str) -> str:
        return canonical(re.sub(r"\s*\(.*?\)|\s+desktop$", "", s.lower()))

    exact = [a for a in apps if norm(a.name) == key]
    if exact:
        return sorted(exact, key=lambda a: (not a.appid, len(a.name)))[0]
    starts = [a for a in apps if norm(a.name).startswith(key) or key.startswith(norm(a.name) + " ")]
    if starts:
        return sorted(starts, key=lambda a: len(a.name))[0]
    words = [a for a in apps if re.search(rf"\b{re.escape(key)}\b", norm(a.name))]
    if words:
        return sorted(words, key=lambda a: len(a.name))[0]
    try:
        from rapidfuzz import fuzz, process
        best = process.extractOne(key, [norm(a.name) for a in apps], scorer=fuzz.WRatio)
        if best and best[1] >= 86:
            return apps[best[2]]
    except ImportError:
        pass
    return None


def suggestions(query: str, limit: int = 3) -> list[str]:
    try:
        from rapidfuzz import fuzz, process
        names = sorted({a.name for a in build_index()})
        return [n for n, score, _ in process.extract(canonical(query), names, scorer=fuzz.WRatio,
                                                     limit=limit) if score >= 60]
    except Exception:
        return []


def launch(app: App):
    if app.appid:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.appid}"], creationflags=_NO_WINDOW)
    elif app.path.lower().endswith("update.exe"):            # Discord
        subprocess.Popen([app.path, "--processStart", "Discord.exe"], creationflags=_NO_WINDOW)
    else:
        os.startfile(app.path)  # type: ignore[attr-defined]


# ── окна ──────────────────────────────────────────────────────────────────────

@dataclass
class Window:
    hwnd: int
    title: str
    pid: int
    exe: str


def list_windows() -> list[Window]:
    """Видимые окна верхнего уровня с заголовком — кроме окон Джарвиса."""
    if not _WIN:
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    wins: list[Window] = []
    me = os.getpid()

    try:
        import psutil
    except ImportError:
        psutil = None

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):   # GW_OWNER
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me or buf.value in ("Program Manager", "Пуск", "Start"):
            return True
        exe = ""
        if psutil:
            try:
                exe = psutil.Process(pid.value).name().lower()
            except Exception:
                pass
        wins.append(Window(int(hwnd), buf.value, pid.value, exe))
        return True

    user32.EnumWindows(_cb, 0)
    return wins


def find_windows(target: str, windows: list[Window] | None = None) -> list[Window]:
    key = canonical(target)
    wins = list_windows() if windows is None else windows
    exe = PROCESS.get(key, key if key.endswith(".exe") else "")
    by_exe = [w for w in wins if exe and w.exe == exe]
    if by_exe:
        return by_exe
    words = {key, (target or "").strip().lower()} - {""}
    return [w for w in wins if any(k in w.title.lower() for k in words)]


def foreground() -> Window | None:
    """Окно впереди, если оно не Джарвиса и не рабочий стол."""
    if not _WIN:
        return None
    import ctypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return next((w for w in list_windows() if w.hwnd == hwnd), None)


def focus(win: Window) -> bool:
    """Вывести окно вперёд. Windows запрещает SetForegroundWindow чужому
    процессу, пока тот не получил «ввод»: короткий нажатый Alt снимает
    запрет. Проверяем, что окно действительно впереди."""
    import ctypes
    user32 = ctypes.windll.user32
    if user32.IsIconic(win.hwnd):
        user32.ShowWindow(win.hwnd, 9)                    # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)                     # Alt вниз
    user32.keybd_event(0x12, 0, 2, 0)                     # Alt вверх
    user32.SetForegroundWindow(win.hwnd)
    time.sleep(0.15)
    return user32.GetForegroundWindow() == win.hwnd


def show(win: Window, how: str) -> None:
    import ctypes
    ctypes.windll.user32.ShowWindow(win.hwnd, {"minimize": 6, "maximize": 3, "restore": 9}[how])


def close(win: Window) -> None:
    """Вежливо закрыть окно (как крестик), не убивая процесс."""
    import ctypes
    ctypes.windll.user32.PostMessageW(win.hwnd, 0x0010, 0, 0)   # WM_CLOSE
