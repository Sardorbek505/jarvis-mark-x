"""JARVIS Mark X — Контроллер браузерных плееров (BrowserMediaController).

Трехуровневая архитектура управления веб-плеерами (VK Видео, YouTube, Кинопоиск):
  Уровень 1: Direct HTML5 Media API через BrowserBridge (точный tab_id)
  Уровень 2: Provider-specific adapter через BrowserBridge
  Уровень 3: Safe Hotkey Fallback (Space, K, M, F, Left, Right, 0..9) при отсутствии моста.
"""

import logging
import platform
import subprocess
import time
from typing import Optional

from core.media.bridge.bridge import BrowserBridge, get_browser_bridge
from core.media.controllers.base import BaseMediaController
from core.media.models import MediaCapabilities, MediaState

logger = logging.getLogger("jarvis-browser-controller")
_OS = platform.system()


def _attach_desktop():
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
            logger.debug("WinSta0 attach note: %s", exc)


class BrowserMediaController(BaseMediaController):
    """Контроллер управления веб-плеерами с точной привязкой по tab_id и гибридным фолбэком."""

    def __init__(
        self,
        window_handle: Optional[int] = None,
        provider_name: str = "browser",
        tab_id: Optional[str] = None,
        window_id: Optional[str] = None,
        browser: str = "chrome",
    ):
        self.window_handle = window_handle
        self.provider_name = provider_name.lower()
        self.tab_id = str(tab_id) if tab_id else None
        self.window_id = str(window_id) if window_id else None
        self.browser = browser
        self._bridge: BrowserBridge = get_browser_bridge()
        self._current_state = MediaState.PLAYING
        self._is_fullscreen = False
        self.session_id: Optional[str] = None

        capabilities = self._bridge.get_dynamic_capabilities(self.tab_id)
        super().__init__(capabilities=capabilities)

    @property
    def capabilities(self) -> MediaCapabilities:
        """Динамически обновляемые возможности в зависимости от подключения моста."""
        return self._bridge.get_dynamic_capabilities(self.tab_id)

    @capabilities.setter
    def capabilities(self, value: MediaCapabilities):
        self._capabilities = value

    def _is_bridge_active(self) -> bool:
        return self._bridge.is_tab_connected(self.tab_id)

    def _get_adapter(self):
        return self._bridge.get_adapter(self.provider_name)

    # ── 1. Воспроизведение и пауза ─────────────────────────────────────────────
    def play(self) -> str:
        if self._is_bridge_active():
            adapter = self._get_adapter()
            if adapter.get_paused(self.tab_id) is False:
                self._current_state = MediaState.PLAYING
                return "Уже воспроизводится, сэр."
            res = adapter.play(self.tab_id)
            if res.get("success"):
                self._current_state = MediaState.PLAYING
                return "Воспроизведение возобновлено, сэр."

        # Hotkey fallback
        if self._current_state == MediaState.PLAYING:
            return "Уже воспроизводится, сэр."
        if self._send_key_safe("space"):
            self._current_state = MediaState.PLAYING
            return "Воспроизведение возобновлено, сэр."
        return "Не удалось запустить плеер, сэр."

    def pause(self) -> str:
        if self._is_bridge_active():
            adapter = self._get_adapter()
            if adapter.get_paused(self.tab_id) is True:
                self._current_state = MediaState.PAUSED
                return "Уже на паузе, сэр."
            res = adapter.pause(self.tab_id)
            if res.get("success"):
                self._current_state = MediaState.PAUSED
                return "Пауза поставлена, сэр."

        # Hotkey fallback
        if self._current_state == MediaState.PAUSED:
            return "Уже на паузе, сэр."
        if self._send_key_safe("space"):
            self._current_state = MediaState.PAUSED
            return "Пауза поставлена, сэр."
        return "Не удалось поставить на паузу, сэр."

    def toggle_playback(self) -> str:
        if self._is_bridge_active():
            adapter = self._get_adapter()
            is_paused = adapter.get_paused(self.tab_id)
            if is_paused is True:
                return self.play()
            elif is_paused is False:
                return self.pause()

        if self._send_key_safe("space"):
            self._current_state = MediaState.PAUSED if self._current_state == MediaState.PLAYING else MediaState.PLAYING
            return "Готово, сэр."
        return "Не удалось переключить плеер, сэр."

    def stop(self) -> str:
        """Остановка воспроизведения и переход на 00:00 БЕЗ закрытия вкладки."""
        if self._is_bridge_active():
            adapter = self._get_adapter()
            adapter.pause(self.tab_id)
            if self.capabilities.seek_absolute:
                adapter.seek_absolute(self.tab_id, 0.0)
        else:
            self.pause()
            self._send_key_safe("0")

        self._current_state = MediaState.STOPPED
        return "Воспроизведение остановлено, сэр."

    def close(self) -> str:
        """Явное закрытие конкретной вкладки плеера по tab_id текущей MediaSession."""
        if self._is_bridge_active() and self.tab_id:
            res = self._bridge.close_tab(self.tab_id)
            if res.get("success"):
                self._current_state = MediaState.STOPPED
                return "Вкладка плеера закрыта, сэр."

        # Hotkey fallback (Ctrl+W)
        if self._focus_safe():
            self._send_key_safe("escape")
            time.sleep(0.1)
            try:
                import pyautogui
                pyautogui.hotkey("ctrl", "w")
            except Exception:
                if _OS == "Windows":
                    cmd = "(New-Object -ComObject WScript.Shell).SendKeys('^w')"
                    subprocess.run(["powershell", "-Command", cmd], capture_output=True, timeout=3)

        self._current_state = MediaState.STOPPED
        return "Закрыл вкладку плеера, сэр."

    # ── 2. Перемотка ─────────────────────────────────────────────────────────
    def seek_absolute(self, seconds: float) -> str:
        """Переход по точному таймкоду (в секундах)."""
        if self._is_bridge_active() and self.capabilities.seek_absolute:
            res = self._get_adapter().seek_absolute(self.tab_id, seconds)
            if res.get("success"):
                return f"Перешёл на {int(seconds)} сек, сэр."
        return "Переход по точному времени не поддерживается для этого веб-плеера, сэр."

    def seek_relative(self, seconds: float) -> str:
        """Относительная перемотка вперёд (+) или назад (-) в секундах."""
        if self._is_bridge_active():
            res = self._get_adapter().seek_relative(self.tab_id, seconds)
            if res.get("success"):
                direction = "вперёд" if seconds > 0 else "назад"
                return f"Перемотал на {abs(int(seconds))} сек {direction}, сэр."

        # Hotkey fallback (Left / Right)
        steps = max(1, int(abs(seconds) // 5))
        key = "right" if seconds > 0 else "left"
        if self._focus_safe():
            for _ in range(steps):
                self._send_key_safe(key)
                time.sleep(0.05)
            direction = "вперёд" if seconds > 0 else "назад"
            return f"Перемотал на {abs(int(seconds))} сек {direction}, сэр."
        return "Не удалось перемотать видео, сэр."

    def seek_percent(self, percent: float) -> str:
        """Переход на процент от длины видео (0.0 .. 1.0 или 0 .. 100)."""
        if percent > 1.0:
            percent /= 100.0
        percent = max(0.0, min(1.0, percent))

        if self._is_bridge_active():
            res = self._get_adapter().seek_percent(self.tab_id, percent)
            if res.get("success"):
                return f"Перешёл на {int(percent * 100)}% фильма, сэр."

        # Hotkey fallback (Клавиши 0..9 для перехода по 10%)
        digit_key = str(int(percent * 10))
        if self._send_key_safe(digit_key):
            return f"Перешёл на {int(percent * 100)}% фильма, сэр."
        return "Не удалось перейти на указанный процент, сэр."

    # ── 3. Громкость и звук ───────────────────────────────────────────────────
    def set_player_volume(self, percent: int) -> str:
        """Установка внутриплеерной громкости HTML5 плеера."""
        if self._is_bridge_active():
            res = self._get_adapter().set_player_volume(self.tab_id, percent)
            if res.get("success"):
                return f"Громкость плеера установлена на {percent}%, сэр."
        return self.set_volume(percent)

    def set_volume(self, percent: int) -> str:
        """Установка внутриплеерной громкости плеера."""
        if self._is_bridge_active():
            res = self._get_adapter().set_player_volume(self.tab_id, percent)
            if res.get("success"):
                return f"Громкость видео установлена на {percent}%, сэр."

        # Hotkey fallback (Up / Down)
        if self._focus_safe():
            steps = 5
            key = "up" if percent > 50 else "down"
            for _ in range(steps):
                self._send_key_safe(key)
                time.sleep(0.05)
            return f"Громкость изменена до ~{percent}%, сэр."
        return "Не удалось изменить громкость плеера, сэр."

    def volume_up(self, step: int = 10) -> str:
        if self._focus_safe():
            for _ in range(max(1, step // 2)):
                self._send_key_safe("up")
                time.sleep(0.05)
            return "Громкость видео увеличена, сэр."
        return "Не удалось увеличить громкость, сэр."

    def volume_down(self, step: int = 10) -> str:
        if self._focus_safe():
            for _ in range(max(1, step // 2)):
                self._send_key_safe("down")
                time.sleep(0.05)
            return "Громкость видео уменьшена, сэр."
        return "Не удалось уменьшить громкость, сэр."

    def mute(self) -> str:
        if self._is_bridge_active():
            adapter = self._get_adapter()
            if adapter.get_muted(self.tab_id) is True:
                return "Звук видео уже выключен, сэр."
            res = adapter.mute(self.tab_id)
            if res.get("success"):
                return "Звук видео выключен, сэр."

        # Hotkey fallback
        if getattr(self, "_is_muted", False):
            return "Звук видео уже выключен, сэр."
        if self._send_key_safe("m"):
            self._is_muted = True
            return "Звук видео выключен, сэр."
        return "Не удалось выключить звук, сэр."

    def unmute(self) -> str:
        if self._is_bridge_active():
            adapter = self._get_adapter()
            if adapter.get_muted(self.tab_id) is False:
                return "Звук видео уже включен, сэр."
            res = adapter.unmute(self.tab_id)
            if res.get("success"):
                return "Звук видео включен, сэр."

        # Hotkey fallback
        if not getattr(self, "_is_muted", False):
            return "Звук видео уже включен, сэр."
        if self._send_key_safe("m"):
            self._is_muted = False
            return "Звук видео включен, сэр."
        return "Не удалось включить звук, сэр."

    def toggle_mute(self) -> str:
        if self._is_bridge_active():
            adapter = self._get_adapter()
            is_muted = adapter.get_muted(self.tab_id)
            if is_muted is True:
                return self.unmute()
            elif is_muted is False:
                return self.mute()

        if self._send_key_safe("m"):
            self._is_muted = not getattr(self, "_is_muted", False)
            return "Звук видео переключён, сэр."
        return "Не удалось переключить звук, сэр."

    # ── 4. Позиция и длительность ─────────────────────────────────────────────
    def get_position(self) -> Optional[float]:
        if self._is_bridge_active():
            return self._get_adapter().get_current_time(self.tab_id)
        return None

    def get_duration(self) -> Optional[float]:
        if self._is_bridge_active():
            return self._get_adapter().get_duration(self.tab_id)
        return None

    def get_remaining_time(self) -> Optional[float]:
        if self._is_bridge_active():
            pos = self.get_position()
            dur = self.get_duration()
            if pos is not None and dur is not None and dur > pos:
                return dur - pos
        return None

    def get_playback_rate(self) -> Optional[float]:
        if self._is_bridge_active():
            return self._get_adapter().get_playback_rate(self.tab_id)
        return 1.0

    def set_playback_rate(self, rate: float) -> str:
        if self._is_bridge_active():
            res = self._get_adapter().set_playback_rate(self.tab_id, rate)
            if res.get("success"):
                return f"Скорость воспроизведения установлена на {rate}x, сэр."
        return "Изменение скорости не поддерживается в этом режиме, сэр."

    # ── 5. Полноэкранный режим ────────────────────────────────────────────────
    def enter_fullscreen(self) -> str:
        if self._send_key_safe("f"):
            self._is_fullscreen = True
            return "Развернул фильм на полный экран, сэр."
        return "Не удалось включить полный экран, сэр."

    def exit_fullscreen(self) -> str:
        if self._send_key_safe("escape") or self._send_key_safe("f"):
            self._is_fullscreen = False
            return "Вышел из полного экрана, сэр."
        return "Не удалось выйти из полного экрана, сэр."

    def toggle_fullscreen(self) -> str:
        return self.enter_fullscreen() if not self._is_fullscreen else self.exit_fullscreen()

    # ── 6. HWND и безопасная фокусировка для Hotkeys ─────────────────────────
    def _matches_provider_title(self, title: str) -> bool:
        """Проверяет соответствие заголовка окна ожидаемому провайдеру."""
        p = self.provider_name.lower()
        t = title.lower()
        if "vk" in p or "вк" in p:
            return any(h in t for h in ("vk video", "vk видео", "vkvideo", "vk.com", "вк видео", "вк"))
        elif "youtube" in p or "ютуб" in p:
            return any(h in t for h in ("youtube", "ютуб"))
        elif "kinopoisk" in p or "кинопоиск" in p:
            return any(h in t for h in ("kinopoisk", "кинопоиск"))
        return any(h in t for h in ("chrome", "edge", "yandex", "opera", "brave", "firefox"))

    def is_window_valid_and_targeted(self, hwnd: Optional[int] = None) -> bool:
        """Проверяет, что окно существует, видимо и принадлежит браузеру/провайдеру."""
        _attach_desktop()
        target_hwnd = hwnd or self.window_handle
        if not target_hwnd or _OS != "Windows":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            if not user32.IsWindow(target_hwnd) or not user32.IsWindowVisible(target_hwnd):
                return False

            rect = wintypes.RECT()
            user32.GetWindowRect(target_hwnd, ctypes.byref(rect))
            if (rect.right - rect.left) <= 300 or (rect.bottom - rect.top) <= 300:
                return False

            cbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(target_hwnd, cbuf, 256)
            cls_name = cbuf.value

            is_browser_class = cls_name in (
                "Chrome_WidgetWin_1",
                "Chrome_Yandex_WidgetWin_1",
                "MozillaWindowClass",
                "OperaWindowClass",
            )
            if not is_browser_class:
                return False

            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(target_hwnd, ctypes.byref(pid))
            proc_name = ""
            if pid.value:
                try:
                    import psutil
                    proc_name = psutil.Process(pid.value).name().lower()
                except Exception:
                    pass

            if proc_name in (
                "chatgpt.exe", "code.exe", "discord.exe", "slack.exe",
                "spotify.exe", "teams.exe", "agy.exe", "cmd.exe",
                "powershell.exe", "windowsterminal.exe"
            ):
                return False

            length = user32.GetWindowTextLengthW(target_hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(target_hwnd, buf, length + 1)
                title = buf.value
                if title:
                    if self._matches_provider_title(title):
                        return True
                    if proc_name in ("browser.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe"):
                        return True

            # Если заголовок пустой (Yandex Browser или загрузка страницы в Chromium),
            # но процесс/класс подтверждён как браузер:
            if proc_name in ("browser.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe") or cls_name == "Chrome_Yandex_WidgetWin_1":
                return True

            return False
        except Exception as e:
            logger.debug("Window validation note: %s", e)
            return False

    def is_foreground_safe(self) -> bool:
        """Проверяет, что активное (foreground) окно принадлежит ожидаемому провайдеру/браузеру."""
        _attach_desktop()
        if _OS != "Windows":
            return False
        try:
            import ctypes
            user32 = ctypes.windll.user32
            fg_hwnd = user32.GetForegroundWindow()
            if not fg_hwnd or not user32.IsWindowVisible(fg_hwnd):
                return False
            if self.window_handle and fg_hwnd == self.window_handle:
                return True
            return self.is_window_valid_and_targeted(fg_hwnd)
        except Exception as e:
            logger.debug("Foreground safety check note: %s", e)
            return False

    def _find_and_verify_window(self) -> Optional[int]:
        _attach_desktop()
        if _OS != "Windows":
            return None

        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        if self.window_handle and self.is_window_valid_and_targeted(self.window_handle):
            return self.window_handle

        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd and self.is_window_valid_and_targeted(fg_hwnd):
            self.window_handle = fg_hwnd
            return fg_hwnd

        found_hwnd = None

        def enum_cb(hwnd, lp):
            nonlocal found_hwnd
            if self.is_window_valid_and_targeted(hwnd):
                found_hwnd = hwnd
                return False
            return True

        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(enum_cb)
        user32.EnumDesktopWindows(0, proc, 0)
        self.window_handle = found_hwnd
        return found_hwnd

    def _focus_safe(self) -> bool:
        hwnd = self._find_and_verify_window()
        if not hwnd or _OS != "Windows":
            return False
        try:
            _attach_desktop()
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            if not self.is_window_valid_and_targeted(hwnd):
                return False

            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.ShowWindow(hwnd, 3)      # SW_MAXIMIZE

            fg_hwnd = user32.GetForegroundWindow()
            cur_thread_id = kernel32.GetCurrentThreadId()
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            fg_pid = wintypes.DWORD()
            fg_thread_id = user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(fg_pid)) if fg_hwnd else 0

            if fg_thread_id and fg_thread_id != cur_thread_id:
                try:
                    user32.AttachThreadInput(cur_thread_id, fg_thread_id, True)
                except Exception:
                    pass

            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)

            user32.keybd_event(0x12, 0, 0, 0)
            user32.SetForegroundWindow(hwnd)
            user32.keybd_event(0x12, 0, 2, 0)

            user32.BringWindowToTop(hwnd)
            user32.SetFocus(hwnd)

            if fg_thread_id and fg_thread_id != cur_thread_id:
                try:
                    user32.AttachThreadInput(cur_thread_id, fg_thread_id, False)
                except Exception:
                    pass

            time.sleep(0.1)
            self.window_handle = hwnd
            return self.is_foreground_safe()
        except Exception as e:
            logger.debug("Focus window error: %s", e)
            return False

    def _send_key_safe(self, key_name: str) -> bool:
        if not self._focus_safe():
            logger.warning("BrowserMediaController: Окно плеера не найдено или активное окно не совпадает с провайдером — клавиша '%s' заблокирована", key_name)
            return False
        try:
            from actions.keyboard import send_key
            return send_key(key_name)
        except Exception as e:
            logger.error("Send key error '%s': %s", key_name, e)
            return False

    def get_state(self) -> MediaState:
        if self._is_bridge_active():
            paused = self._get_adapter().get_paused(self.tab_id)
            if paused is False:
                return MediaState.PLAYING
            elif paused is True:
                return MediaState.PAUSED
        return self._current_state