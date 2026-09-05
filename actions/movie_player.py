"""
Действие: продвинутое управление кино-плеером через VK Видео (vkvideo.ru).

Поддерживает:
  1. Запуск фильмов на vkvideo.ru:
     - открытие поисковой выдачи
     - автоматический переход и клик по 1-й видеокарточке (первому результату)
     - автоматическое разворачивание на полный экран (F / двойной клик)
  2. Полноценная перемотка времени:
     - вперед / назад на N секунд или M минут (seek_forward / seek_back)
     - переход на позицию/процент: начало (0%), середина (50%), конец (90%)
  3. Управление воспроизведением:
     - Space: пауза/продолжить
     - F: переключение полного экрана
     - Громкость плеера и системы
     - Esc + Ctrl+W: выход из фильма
"""

import logging
import platform
import re
import subprocess
import time
import urllib.parse

from actions.browser_control import browser_control
from actions.computer_settings import computer_settings
from actions.keyboard import send_key as _send_key

_logger = logging.getLogger(__name__)
_OS = platform.system()

# ─── Опциональная зависимость pyautogui ───────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False


def _attach_desktop():
    """Подключает поток к интерактивному десктопу WinSta0\\Default на Windows."""
    if _OS == "Windows":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            h_winsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
            if h_winsta:
                user32.SetProcessWindowStation(h_winsta)
                h_desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
                if h_desk:
                    user32.SetThreadDesktop(h_desk)
        except Exception as exc:
            _logger.debug("WinSta0 attach note: %s", exc)


def _find_browser_window():
    """Ищет окно браузера с плеером или сайтом (YouTube, Кинопоиск, VK Видео)."""
    _attach_desktop()
    if _OS == "Windows":
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            found_hwnds = []
            browser_hints = (
                "youtube", "ютуб", "kinopoisk", "кинопоиск",
                "vk video", "vk видео", "vk", "видео", "yandex",
                "chrome", "edge", "firefox", "opera", "brave"
            )

            def enum_cb(hwnd, lp):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        t = buf.value.lower()
                        for hint in browser_hints:
                            if hint in t:
                                rect = wintypes.RECT()
                                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                                w = rect.right - rect.left
                                h = rect.bottom - rect.top
                                if w > 300 and h > 300:
                                    found_hwnds.append((hwnd, buf.value, rect))
                                    break
                return True

            proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(enum_cb)
            user32.EnumWindows(proc, 0)
            if found_hwnds:
                # В первую очередь ищем окна с медиа-сервисами в заголовке
                for hwnd, title, rect in found_hwnds:
                    if any(k in title.lower() for k in ("youtube", "ютуб", "kinopoisk", "кинопоиск", "vk", "видео", "video")):
                        return hwnd, rect
                return found_hwnds[0][0], found_hwnds[0][2]
        except Exception as exc:
            _logger.debug("Win32 find window error: %s", exc)
    return None, None


def _focus_movie_player() -> bool:
    """Активирует и максимизирует окно браузера с плеером."""
    _attach_desktop()
    hwnd, _ = _find_browser_window()
    if hwnd:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.15)
            return True
        except Exception as exc:
            _logger.debug("ShowWindow error: %s", exc)

    # Запасной вариант pygetwindow
    try:
        import pygetwindow as gw
        for win in gw.getAllWindows():
            if any(k in (win.title or "").lower() for k in ("youtube", "ютуб", "kinopoisk", "кинопоиск", "vk", "video", "chrome", "edge", "yandex")):
                try:
                    if win.isMinimized:
                        win.restore()
                    win.maximize()
                    win.activate()
                    return True
                except Exception:
                    pass
    except Exception:
        pass
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
def _play_youtube(title: str, player=None) -> str:
    """Запуск видео на YouTube: поиск, клик по 1-му видео, полноэкранный режим."""
    if player:
        player.write_log(f"SYS: 🎬 YouTube — поиск «{title}»")

    encoded = urllib.parse.quote(title)
    url = f"https://www.youtube.com/results?search_query={encoded}"

    try:
        browser_control({"action": "go_to", "url": url}, player=player)
        time.sleep(2.0)

        _focus_movie_player()
        time.sleep(0.3)

        if _HAS_PYAUTOGUI:
            sw, sh = pyautogui.size()
            card_x = int(sw * 0.40)
            card_y = int(sh * 0.38)
            pyautogui.click(card_x, card_y)
            if player:
                player.write_log(f"SYS: 🎬 Запуск видео на YouTube ({card_x}, {card_y})")
            time.sleep(1.8)
        else:
            _send_key("enter")
            time.sleep(1.8)

        _fullscreen(player=player)
        return f"Включаю «{title}» на YouTube в полный экран, приятного просмотра, сэр."
    except Exception as e:
        _logger.error("YouTube open error: %s", e)
        return "Не удалось открыть видео на YouTube, сэр."


def _play_kinopoisk(title: str, player=None) -> str:
    """Поиск фильма на Кинопоиске и открытие карточки."""
    if player:
        player.write_log(f"SYS: 🎬 Кинопоиск — поиск «{title}»")

    encoded = urllib.parse.quote(title)
    url = f"https://www.kinopoisk.ru/index.php?kp_query={encoded}"

    try:
        browser_control({"action": "go_to", "url": url}, player=player)
        time.sleep(2.5)

        _focus_movie_player()
        time.sleep(0.3)

        if _HAS_PYAUTOGUI:
            sw, sh = pyautogui.size()
            card_x = int(sw * 0.35)
            card_y = int(sh * 0.36)
            pyautogui.click(card_x, card_y)
            if player:
                player.write_log(f"SYS: 🎬 Открытие фильма на Кинопоиске ({card_x}, {card_y})")
        else:
            _send_key("enter")

        return f"Открываю фильм «{title}» на Кинопоиске, сэр."
    except Exception as e:
        _logger.error("Kinopoisk open error: %s", e)
        return "Не удалось открыть фильм на Кинопоиске, сэр."


def _play_vkvideo(title: str, player=None) -> str:
    """Открыть фильм на vkvideo.ru, кликнуть 1-ю карточку видео и развернуть на полный экран."""
    if player:
        player.write_log(f"SYS: 🎬 VK Видео — поиск «{title}»")

    encoded = urllib.parse.quote(f"{title} фильм")
    url = f"https://vkvideo.ru/?q={encoded}&section=search"

    try:
        browser_control({"action": "go_to", "url": url}, player=player)
        time.sleep(2.5)

        _focus_movie_player()
        time.sleep(0.3)

        if _HAS_PYAUTOGUI:
            sw, sh = pyautogui.size()
            card_x = int(sw * 0.38)
            card_y = int(sh * 0.41)
            pyautogui.click(card_x, card_y)
            if player:
                player.write_log(f"SYS: 🎬 Запуск 1-го фильма ({card_x}, {card_y})")
            time.sleep(2.0)
        else:
            _send_key("enter")
            time.sleep(2.0)

        _fullscreen(player=player)
        return f"Включаю фильм «{title}» на VK Видео в полный экран, приятного просмотра, сэр."
    except Exception as e:
        _logger.error("VK Video open error: %s", e)
        return "Не удалось открыть фильм на VK Видео, сэр."


def _play(title: str, platform: str = "auto", player=None) -> str:
    """
    Открыть фильм или видео на платформе (YouTube, Кинопоиск, VK Видео или авто-выбор).
    """
    title = (title or "").strip()
    if not title:
        return "Назовите фильм, сэр."

    target_platform = (platform or "auto").strip().lower()
    lower_title = title.lower()

    # Определение платформы из текста запроса и очистка названия
    if any(k in lower_title for k in ("на ютубе", "на youtube", "в ютубе", "в youtube", "ютуб", "youtube")):
        target_platform = "youtube"
        title = re.sub(r"(?i)\b(?:на\s+|в\s+)?(?:ютубе?|youtube)\b", "", title).strip()
    elif any(k in lower_title for k in ("на кинопоиске", "в кинопоиске", "кинопоиск")):
        target_platform = "kinopoisk"
        title = re.sub(r"(?i)\b(?:на\s+|в\s+)?кинопоиске?\b", "", title).strip()
    elif any(k in lower_title for k in ("на вк видео", "на вк", "вк видео", "vk video", "vk", "вк")):
        target_platform = "vkvideo"
        title = re.sub(r"(?i)\b(?:на\s+|в\s+)?(?:вк\s+видео|vk\s+video|vk|вк)\b", "", title).strip()
    elif target_platform == "auto":
        # Если платформа не указана явно:
        # Трейлеры, клипы, обзоры, шоу -> YouTube
        if any(k in lower_title for k in ("трейлер", "клип", "обзор", "интервью", "шоу", "подкаст", "стрим")):
            target_platform = "youtube"
        else:
            target_platform = "vkvideo"

    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        title = "фильм"

    if target_platform == "youtube":
        return _play_youtube(title, player=player)
    elif target_platform == "kinopoisk":
        return _play_kinopoisk(title, player=player)
    else:
        return _play_vkvideo(title, player=player)


def _pause_resume(player=None) -> str:
    """Space — переключение пауза/воспроизведение."""
    _focus_movie_player()
    if _send_key("space"):
        if player:
            player.write_log("SYS: ⏯ Пауза/Воспроизведение")
        return "Готово, сэр."
    return "Не могу управлять плеером, сэр."


def _fullscreen(player=None) -> str:
    """F — переключение в полный экран в VK Video / YouTube / Кинопоиск."""
    _focus_movie_player()
    time.sleep(0.1)

    # Уровень 1: _send_key("f") через pyautogui / WScript.Shell
    if _send_key("f"):
        if player:
            player.write_log("SYS: ⛶ Полный экран")
        return "Развернул фильм на полный экран, сэр."

    # Уровень 2: Прямой Win32 keybd_event (VK_F = 0x46)
    if _OS == "Windows":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.keybd_event(0x46, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(0x46, 0, 2, 0)
            if player:
                player.write_log("SYS: ⛶ Полный экран")
            return "Развернул фильм на полный экран, сэр."
        except Exception:
            pass

    return "Полный экран, сэр."


def _seek_forward(seconds: float = 0, minutes: float = 0, player=None) -> str:
    """Перемотка фильма вперёд на указанное количество секунд/минут."""
    _focus_movie_player()
    total_seconds = float(seconds or 0) + (float(minutes or 0) * 60)
    if total_seconds <= 0:
        total_seconds = 10.0  # По умолчанию 10 секунд

    # В плеере VK Видео одно нажатие стрелки вправо = 5 секунд
    press_count = max(1, round(total_seconds / 5.0))

    if _HAS_PYAUTOGUI:
        for _ in range(press_count):
            pyautogui.press("right")
            time.sleep(0.015)
    else:
        for _ in range(press_count):
            _send_key("right")
            time.sleep(0.02)

    msg = f"Перемотал вперёд на {int(minutes)} мин., сэр." if minutes > 0 else f"Перемотал вперёд на {int(total_seconds)} сек., сэр."
    if player:
        player.write_log(f"SYS: ⏩ {msg}")
    return msg


def _seek_back(seconds: float = 0, minutes: float = 0, player=None) -> str:
    """Перемотка фильма назад на указанное количество секунд/минут."""
    _focus_movie_player()
    total_seconds = float(seconds or 0) + (float(minutes or 0) * 60)
    if total_seconds <= 0:
        total_seconds = 10.0  # По умолчанию 10 секунд

    # В плеере VK Видео одно нажатие стрелки влево = 5 секунд
    press_count = max(1, round(total_seconds / 5.0))

    if _HAS_PYAUTOGUI:
        for _ in range(press_count):
            pyautogui.press("left")
            time.sleep(0.015)
    else:
        for _ in range(press_count):
            _send_key("left")
            time.sleep(0.02)

    msg = f"Перемотал назад на {int(minutes)} мин., сэр." if minutes > 0 else f"Перемотал назад на {int(total_seconds)} сек., сэр."
    if player:
        player.write_log(f"SYS: ⏪ {msg}")
    return msg


def _seek_to_position(position: str, player=None) -> str:
    """Перемотка на процент или позицию (начало, середина, 50%, 75%)."""
    _focus_movie_player()
    pos = (position or "").strip().lower()

    ratio = 0.5
    if any(k in pos for k in ("начал", "сначала", "0")):
        ratio = 0.02
    elif any(k in pos for k in ("конец", "конц", "финал", "99")):
        ratio = 0.98
    elif any(k in pos for k in ("середин", "пол", "половин", "50")):
        ratio = 0.50
    else:
        m = re.search(r"(\d+)", pos)
        if m:
            val = float(m.group(1))
            ratio = max(0.01, min(0.99, val / 100.0 if val > 1 else val))

    if _HAS_PYAUTOGUI:
        sw, sh = pyautogui.size()
        tx = int(sw * ratio)
        ty = sh - 45  # Полоса таймлайна внизу экрана
        pyautogui.moveTo(tx, ty)
        time.sleep(0.1)
        pyautogui.click(tx, ty)
        pyautogui.moveTo(10, 10)  # Убираем курсор в угол

    percent_int = int(round(ratio * 100))
    msg = f"Перемотал на {percent_int}% фильма, сэр."
    if player:
        player.write_log(f"SYS: ⏱ {msg}")
    return msg


def _exit_movie(player=None) -> str:
    """Выход из режима фильма: Esc → Ctrl+W."""
    _focus_movie_player()
    _send_key("escape")
    time.sleep(0.2)

    if _send_hotkey_ctrl_w():
        if player:
            player.write_log("SYS: ✕ Закрыт фильм")
        return "Закрываю фильм, сэр."

    return "Выхожу из режима фильма, сэр."


def _volume(direction: str, player=None) -> str:
    """Громкость в плеере и системная громкость."""
    _focus_movie_player()
    _send_key("up" if direction == "up" else "down")
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
        action:   play | pause | resume | fullscreen | seek_forward |
                  seek_back | seek_to | volume_up | volume_down | exit
        title:    название фильма для action=play
        seconds:  секунды для перемотки
        minutes:  минуты для перемотки
        position: позиция фильма ('начало', 'середина', '50%')
    """
    action = (parameters.get("action") or "").strip().lower()
    title = (parameters.get("title") or "").strip()
    platform = (parameters.get("platform") or "auto").strip().lower()
    seconds = parameters.get("seconds") or 0
    minutes = parameters.get("minutes") or 0
    position = parameters.get("position") or parameters.get("timecode") or ""

    try:
        seconds = float(seconds)
    except (ValueError, TypeError):
        seconds = 0

    try:
        minutes = float(minutes)
    except (ValueError, TypeError):
        minutes = 0

    # ── Воспроизведение нового фильма ─────────────────────────────────────────
    if action in ("play", "start", "запустить", "включить"):
        return _play(title, platform=platform, player=player)

    # ── Управление текущим воспроизведением ───────────────────────────────────
    elif action in ("pause", "resume", "toggle", "пауза", "продолжай", "стоп"):
        return _pause_resume(player)

    elif action in ("fullscreen", "full_screen", "полный_экран", "развернуть"):
        return _fullscreen(player)

    elif action in ("seek_forward", "forward", "вперёд", "вперед", "дальше"):
        return _seek_forward(seconds=seconds, minutes=minutes, player=player)

    elif action in ("seek_back", "back", "rewind", "назад", "отмотай"):
        return _seek_back(seconds=seconds, minutes=minutes, player=player)

    elif action in ("seek_to", "seek_position", "jump_to", "position", "перейти"):
        return _seek_to_position(position=position, player=player)

    elif action in ("volume_up", "louder", "громче"):
        return _volume("up", player)

    elif action in ("volume_down", "quieter", "тише"):
        return _volume("down", player)

    elif action in ("exit", "close", "stop", "выход", "закрыть"):
        return _exit_movie(player)

    return f"Не понял команду плеера: «{action}»."
