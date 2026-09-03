"""Tests for GlobalHotkeyManager."""

import time
import pytest
from core.hotkey_manager import GlobalHotkeyManager


def test_hotkey_manager_lifecycle():
    called = []
    mgr = GlobalHotkeyManager(on_wake=lambda: called.append("wake"), on_mute=lambda: called.append("mute"))
    mgr.start()
    assert mgr._thread is not None
    assert mgr._thread.is_alive()
    time.sleep(0.1)
    mgr.stop()
    time.sleep(0.1)
