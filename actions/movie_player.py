"""
Действие: продвинутое управление кино-плеером.

Работает с kinogo.mu — поиск и воспроизведение фильмов.
Использует универсальные клавиши, которые поддерживают все плееры:
  Space — пауза/продолжить
  F     — полный экран
  ←  →  — перемотка ±10 сек
  Esc   — выход из полного экрана
  Ctrl+W — закрыть вкладку (выход из режима)

Для отправки клавиш — двухуровневая стратегия:
  1. pyautogui (если установлен) — быстрый и кросс-платформенный
  2. PowerShell SendKeys (Windows fallback) — без зависимостей

Если ни то ни другое не доступно — graceful degradation,
говорим пользователю что не можем управлять плеером.
"""

import platform
import re
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Optional

from actions.browser_control import browser_control
from actions.computer_settings import computer_settings

import logging

_logger = logging.getLogger(__name__)

_OS = platform.system()

# ─── Опциональная зависимость pyautogui ───────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False  # Не падать если мышь в углу экрана
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False


# ─── Карта клавиш: общее имя → (pyautogui, PowerShell SendKeys) ───────────────
_KEY_MAP = {
    "space":      ("space",      " "),
    "f":          ("f",          "f"),
    "right":      ("right",      "{RIGHT}"),
    "left":       ("left",       "{LEFT}"),
    "up":         ("up",         "{UP}"),
    "down":       ("down",       "{DOWN}"),
    "escape":     ("escape",     "{ESC}"),
}


def _send_key(key: str) -> bool:
    """
    Отправляет одиночную клавишу в активное окно.
    Возвращает True если получилось.
    """
    if key not in _KEY_MAP:
        return False

    py_key, ps_key = _KEY_MAP[key]

    # Попытка 1: pyautogui (быстрая, кросс-платформенная)
    if _HAS_PYAUTOGUI:
        try:
            pyautogui.press(py_key)
            return True
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    # Попытка 2: PowerShell SendKeys (только Windows)
    if _OS == "Windows":
        try:
            cmd = f"(New-Object -ComObject WScript.Shell).SendKeys('{ps_key}')"
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True, timeout=3
            )
            return result.returncode == 0
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    return False


def _send_hotkey_ctrl_w() -> bool:
    """Закрыть текущую вкладку браузера (Ctrl+W)."""
    if _HAS_PYAUTOGUI:
        try:
            pyautogui.hotkey("ctrl", "w")
            return True
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    if _OS == "Windows":
        try:
            cmd = "(New-Object -ComObject WScript.Shell).SendKeys('^w')"
            result = subprocess.run(
                ["powershell", "-Command", cmd],
                capture_output=True, timeout=3
            )
            return result.returncode == 0
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    return False


# ─── Действия плеера ──────────────────────────────────────────────────────────
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _scrape_first_youtube_video(query: str) -> Optional[str]:
    """
    HTTP scraping YouTube — самый надёжный путь.
    YouTube HTML содержит JSON блок с videoId первого результата.
    """
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # YouTube videoId это 11 символов из [a-zA-Z0-9_-]
        match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if match:
            video_id = match.group(1)
            return f"https://www.youtube.com/watch?v={video_id}"
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return None


def _scrape_first_kinogo_video(query: str) -> Optional[str]:
    """
    HTTP scraping kinogo.mu — поиск прямого URL фильма.
    """
    try:
        import http.cookiejar
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        opener.addheaders = list(_BROWSER_HEADERS.items())

        encoded = urllib.parse.quote(query)
        url = f"https://lim.kinogo.mu/search/{encoded}"
        with opener.open(url, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Ищем ссылки на фильмы на kinogo.mu
        patterns = [
            r'href="(/film/[^"]*)"',
            r'href="(/series/[^"]*)"',
            r'href="(https://lim\.kinogo\.mu/film/[^"]*)"',
            r'href="(https://lim\.kinogo\.mu/series/[^"]*)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                video_url = match.group(1)
                if not video_url.startswith("http"):
                    video_url = f"https://lim.kinogo.mu{video_url}"
                return video_url
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
    return None


def _play(title: str, player=None) -> str:
    """
    Открыть фильм через vkvideo.ru.

    Открывает страницу поиска vkvideo.ru по названию прямо в браузере.
    Автозапуск первого результата у VK ненадёжен (JS + вход в аккаунт),
    поэтому открываем поиск — с активной VK-сессией видео в один клик.
    """
    title = (title or "").strip()
    if not title:
        return "Назовите фильм, сэр."

    if player:
        player.write_log(f"SYS: vkvideo.ru — поиск «{title}»")

    encoded = urllib.parse.quote(title)
    url = f"https://vkvideo.ru/?q={encoded}&section=search"
    try:
        browser_control({"action": "go_to", "url": url}, player=player)
        return f"Открыл «{title}» на vkvideo.ru, сэр."
    except Exception as e:
        _logger.error("vkvideo.ru open failed: %s", e)
        return "Не удалось открыть vkvideo.ru, сэр."


def _selenium_select_first_video(search_url: str, player=None) -> Optional[str]:
    """
    Использовать Selenium для автоматического выбора первого видео в поиске kinogo.mu.
    Возвращает URL фильма если успешно, None если нет.

    Гарантирует driver.quit() через try/finally — Chrome не остаётся висеть
    в фоне при любой ошибке.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        if player:
            player.write_log("SYS: ✗ Selenium не установлен")
        return None

    driver = None
    try:
        # Настройка Chrome в headless режиме
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        if player:
            player.write_log("SYS: → Chrome запущен, открываю страницу")

        driver.get(search_url)

        if player:
            player.write_log("SYS: → Жду загрузки (5 сек)")
        time.sleep(5)

        selectors_to_try = [
            'a[href*="/film/"]',
            'a[href*="/series/"]',
            '.item',
            '[class*="poster"]',
        ]

        for selector in selectors_to_try:
            try:
                if player:
                    player.write_log(f"SYS: → Пробую селектор: {selector}")

                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    if player:
                        player.write_log(f"SYS: → Найдено {len(elements)} элементов")

                    elements[0].click()
                    if player:
                        player.write_log("SYS: → Клик выполнен")

                    time.sleep(3)

                    current_url = driver.current_url
                    if '/film/' in current_url or '/series/' in current_url:
                        if player:
                            player.write_log(f"SYS: ▶ Видео открыто: {current_url}")
                        return current_url

            except Exception as e:
                if player:
                    player.write_log(f"SYS: ✗ Селектор {selector} не сработал: {e}")
                continue

        return None

    except Exception as e:
        if player:
            player.write_log(f"SYS: ✗ Ошибка Selenium: {e}")
        return None
    finally:
        # Гарантированный cleanup — Chrome процесс не остаётся висеть
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)


def _pause_resume(player=None) -> str:
    """Space — переключение пауза/воспроизведение."""
    if _send_key("space"):
        if player:
            player.write_log("SYS: ⏯ Пауза/Воспроизведение")
        return "Готово."
    return "Не могу управлять плеером, сэр. Установите pyautogui."


def _fullscreen(player=None) -> str:
    """F — полный экран в YouTube/VK Video."""
    if _send_key("f"):
        if player:
            player.write_log("SYS: ⛶ Полный экран")
        return "Полный экран."
    return "Не могу включить полный экран, сэр."


def _seek_forward(player=None) -> str:
    """Стрелка вправо — вперёд 10 секунд (стандарт YouTube/VK)."""
    if _send_key("right"):
        if player:
            player.write_log("SYS: ⏩ Вперёд 10 сек")
        return "Перематываю вперёд."
    return "Не получилось перемотать, сэр."


def _seek_back(player=None) -> str:
    """Стрелка влево — назад 10 секунд."""
    if _send_key("left"):
        if player:
            player.write_log("SYS: ⏪ Назад 10 сек")
        return "Назад на десять секунд."
    return "Не получилось перемотать, сэр."


def _exit_movie(player=None) -> str:
    """Выход из режима фильма: Esc → Ctrl+W."""
    # Сначала выход из полноэкранного режима
    _send_key("escape")
    time.sleep(0.15)

    # Затем закрытие вкладки
    if _send_hotkey_ctrl_w():
        if player:
            player.write_log("SYS: ✕ Закрыт режим фильма")
        return "Закрываю режим фильма, сэр."

    return "Выхожу из режима фильма, сэр."


def _volume(direction: str, player=None) -> str:
    """Громкость через существующий computer_settings (системная громкость)."""
    action = "увеличить громкость" if direction == "up" else "уменьшить громкость"
    return computer_settings(
        {"action": action, "value": "10"},
        player=player,
    )


# ─── Публичная точка входа ────────────────────────────────────────────────────
def movie_player(parameters: dict, player=None) -> str:
    """
    Главная точка входа для tool 'movie_player'.

    parameters:
        action: play | pause | resume | fullscreen | seek_forward |
                seek_back | volume_up | volume_down | exit
        title:  название фильма для action=play
    """
    action = (parameters.get("action") or "").strip().lower()
    title = (parameters.get("title") or "").strip()

    # ── Воспроизведение нового фильма ─────────────────────────────────────────
    if action in ("play", "start", "запустить", "включить"):
        return _play(title, player)

    # ── Управление текущим воспроизведением ───────────────────────────────────
    elif action in ("pause", "resume", "toggle", "пауза", "продолжай"):
        return _pause_resume(player)

    elif action in ("fullscreen", "full_screen", "полный_экран"):
        return _fullscreen(player)

    elif action in ("seek_forward", "forward", "вперёд", "вперед"):
        return _seek_forward(player)

    elif action in ("seek_back", "back", "rewind", "назад"):
        return _seek_back(player)

    elif action in ("volume_up", "louder", "громче"):
        return _volume("up", player)

    elif action in ("volume_down", "quieter", "тише"):
        return _volume("down", player)

    elif action in ("exit", "close", "stop", "выход", "закрыть"):
        return _exit_movie(player)

    return f"Не понял команду плеера: «{action}»."
