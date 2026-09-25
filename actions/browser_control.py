"""
Действие: управление браузером — открыть сайт, поиск
"""

import subprocess
import sys
import os
import urllib.parse
import shutil

import logging

_logger = logging.getLogger(__name__)


_BROWSERS = {
    "chrome": ["google-chrome", "chrome", "chromium"],
    "firefox": ["firefox"],
    "edge": ["msedge", "microsoft-edge"],
    "safari": ["safari"],
    "opera": ["opera"],
    "brave": ["brave-browser", "brave"],
}

_SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={}",
    "yandex": "https://yandex.ru/search/?text={}",
    "duckduckgo": "https://duckduckgo.com/?q={}",
    "bing": "https://www.bing.com/search?q={}",
}


def _is_safe_url(url: str) -> bool:
    """
    Разрешает только http(s) URL. Блокирует file://, javascript:,
    локальные .exe пути — защита от хака через подмену URL.
    """
    if not url or not isinstance(url, str):
        return False
    low = url.strip().lower()
    if low.startswith(("http://", "https://")):
        return True
    return False


_WIN_BROWSERS = {
    # имя → (каноническое имя в core.win_apps, exe)
    "chrome": ("chrome", "chrome.exe"), "хром": ("chrome", "chrome.exe"),
    "edge": ("edge", "msedge.exe"), "firefox": ("firefox", "firefox.exe"),
    "opera": ("opera", "opera.exe"), "brave": ("brave", "brave.exe"),
    "yandex": ("yandex", "browser.exe"), "яндекс": ("yandex", "browser.exe"),
}


def _browser_exe(browser: str) -> str | None:
    """Путь к exe названного браузера. На Windows `chrome` нет в PATH —
    раньше «открой в хроме» молча открывало браузер по умолчанию."""
    entry = _WIN_BROWSERS.get(browser.lower())
    if not entry:
        return None
    canon, exe = entry
    from core import win_apps
    for p in win_apps._KNOWN_PATHS.get(canon, []):
        full = os.path.expandvars(p)
        if os.path.isfile(full):
            return full
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}")
                path = winreg.QueryValue(key, None)
                if path and os.path.isfile(path.strip('"')):
                    return path.strip('"')
            except OSError:
                continue
    except ImportError:
        pass
    return shutil.which(exe)


def _open_url(url: str, browser: str | None = None) -> bool:
    """Открывает URL в браузере (только http/https). True — открылся."""
    if not _is_safe_url(url):
        print(f"[browser] ⛔ Отклонён небезопасный URL: {url[:80]}")
        return False

    if browser:
        # Только известные браузеры. Раньше неизвестное имя запускалось как
        # есть: browser="powershell", url="https://x;Start-Process calc" —
        # и выполнялась произвольная команда.
        exe = _browser_exe(browser) if sys.platform == "win32" else None
        candidates = [exe] if exe else [c for c in _BROWSERS.get(browser.lower(), []) if shutil.which(c)]
        for cmd in candidates:
            try:
                subprocess.Popen([cmd, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except OSError as exc:
                _logger.debug("Браузер %s: %s", cmd, exc)

    # Дефолтный браузер
    if sys.platform == "win32":
        try:
            os.startfile(url)
            return True
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    # Не `cmd /c start`: cmd сам разбирает строку, и `&` в URL выполнял бы
    # команду (и обрезал обычные ссылки с ?a=1&b=2).
    import webbrowser
    try:
        return bool(webbrowser.open(url))
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
        return False


def browser_control(parameters: dict, player=None) -> str:
    action = parameters.get("action", "go_to").lower()
    browser = parameters.get("browser")
    url = parameters.get("url", "")
    query = parameters.get("query", "")
    engine = parameters.get("engine", "google").lower()

    try:
        if action == "go_to" and url:
            if not url.startswith("http"):
                url = "https://" + url
            if not _open_url(url, browser):
                return f"Не получилось открыть {url}."
            if player:
                player.write_log(f"SYS: Браузер → {url}")
            return f"Открыл {url}."

        elif action == "search" and query:
            template = _SEARCH_ENGINES.get(engine, _SEARCH_ENGINES["google"])
            search_url = template.format(urllib.parse.quote(query))
            if not _open_url(search_url, browser):
                return "Не получилось открыть браузер."
            if player:
                player.write_log(f"SYS: Поиск в браузере → {query}")
            return f"Открыл поиск «{query}» в браузере."

        elif url:
            return f"Открыл {url}." if _open_url(url, browser) else f"Не получилось открыть {url}."
        return "Укажите URL или поисковый запрос."

    except Exception as e:
        return f"Ошибка браузера: {e}"
