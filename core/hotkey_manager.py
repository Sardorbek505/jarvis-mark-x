"""Global Windows Hotkey Manager for JARVIS Mark X.

Uses native Win32 user32.RegisterHotKey for zero-latency, reliable system-wide shortcuts:
- F8: Wake JARVIS / Bring to front / Unmute and listen
- Ctrl+Shift+J: Alternative wake shortcut
- Ctrl+Shift+M: Toggle mute globally from any app/game
"""

import ctypes
from ctypes import wintypes
import logging
import sys
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

VK_F8 = 0x77
VK_J = 0x4A
VK_M = 0x4D

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


class GlobalHotkeyManager:
    """Listens for global Windows shortcuts in a background daemon thread."""

    def __init__(self, on_wake: Optional[Callable] = None, on_mute: Optional[Callable] = None):
        self.on_wake = on_wake
        self.on_mute = on_mute
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._thread_id: Optional[int] = None

    def start(self):
        if sys.platform != "win32":
            return
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._message_loop, name="GlobalHotkeys", daemon=True)
        self._thread.start()
        logger.info("[Hotkeys] Global hotkeys active: F8 / Ctrl+Shift+J (Wake), Ctrl+Shift+M (Mute)")

    def stop(self):
        self._running = False
        if self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass

    def _message_loop(self):
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        # ID 1: F8
        user32.RegisterHotKey(None, 1, MOD_NOREPEAT, VK_F8)
        # ID 2: Ctrl+Shift+J
        user32.RegisterHotKey(None, 2, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_J)
        # ID 3: Ctrl+Shift+M
        user32.RegisterHotKey(None, 3, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_M)

        msg = wintypes.MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                if msg.message == WM_HOTKEY:
                    hotkey_id = msg.wParam
                    if hotkey_id in (1, 2):
                        if self.on_wake:
                            try:
                                self.on_wake()
                            except Exception as exc:
                                logger.warning("Error in on_wake callback: %s", exc)
                    elif hotkey_id == 3:
                        if self.on_mute:
                            try:
                                self.on_mute()
                            except Exception as exc:
                                logger.warning("Error in on_mute callback: %s", exc)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, 1)
            user32.UnregisterHotKey(None, 2)
            user32.UnregisterHotKey(None, 3)
