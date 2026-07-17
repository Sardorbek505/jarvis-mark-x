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


def _open_url(url: str, browser: str | None = None):
    """Открывает URL в браузере (только http/https)."""
    if not _is_safe_url(url):
        print(f"[browser] ⛔ Отклонён небезопасный URL: {url[:80]}")
        return

    if browser:
        candidates = _BROWSERS.get(browser.lower(), [browser])
        for cmd in candidates:
            if shutil.which(cmd):
                subprocess.Popen([cmd, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return

    # Дефолтный браузер
    if sys.platform == "win32":
        # Метод 1: os.startfile (прямой запуск через системную ассоциацию)
        try:
            os.startfile(url)
            return
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
        # Метод 2: cmd.exe через список аргументов (shell=False) —
        # безопасно, поскольку URL передаётся как отдельный аргумент,
        # а не интерполируется в shell-строку.
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", url],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
            _open_url(url, browser)
            if player:
                player.write_log(f"SYS: Браузер → {url}")
            return f"Открываю {url}."

        elif action == "search" and query:
            template = _SEARCH_ENGINES.get(engine, _SEARCH_ENGINES["google"])
            search_url = template.format(urllib.parse.quote(query))
            _open_url(search_url, browser)
            if player:
                player.write_log(f"SYS: Поиск в браузере → {query}")
            return f"Ищу «{query}» в браузере."

        else:
            # Если передан просто URL без action
            if url:
                _open_url(url, browser)
                return f"Открываю {url}."
            return "Укажите URL или поисковый запрос."

    except Exception as e:
        return f"Ошибка браузера: {e}"
