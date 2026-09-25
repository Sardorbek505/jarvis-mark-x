"""Tests for GlobalHotkeyManager."""

import sys
import time

import pytest

from core.hotkey_manager import GlobalHotkeyManager


@pytest.mark.skipif(sys.platform != "win32", reason="глобальные хоткеи — только Win32 API")
def test_hotkey_manager_lifecycle():
    called = []
    mgr = GlobalHotkeyManager(on_wake=lambda: called.append("wake"), on_mute=lambda: called.append("mute"))
    mgr.start()
    assert mgr._thread is not None
    assert mgr._thread.is_alive()
    time.sleep(0.1)
    mgr.stop()
    time.sleep(0.1)
