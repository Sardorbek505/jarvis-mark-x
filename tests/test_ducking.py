import os
import time
from unittest.mock import MagicMock, patch
import pytest

from core.ducking_controller import DuckingController, DuckingState
from core.headless_ui import HeadlessUI
from main import Jarvis


def test_is_jarvis_or_system_process_exclusions():
    dc = DuckingController(duck_ratio=0.2)
    try:
        # Own PID
        own_session = MagicMock()
        own_session.ProcessId = os.getpid()
        own_session.Process.name.return_value = "custom.exe"
        assert dc._is_jarvis_or_system_process(own_session) is True

        # Python interpreter
        python_session = MagicMock()
        python_session.ProcessId = 99999
        python_session.Process.name.return_value = "python.exe"
        assert dc._is_jarvis_or_system_process(python_session) is True

        # Jarvis process
        jarvis_session = MagicMock()
        jarvis_session.ProcessId = 88888
        jarvis_session.Process.name.return_value = "jarvis_service.exe"
        assert dc._is_jarvis_or_system_process(jarvis_session) is True

        # Windows Audio DG / System
        audiodg_session = MagicMock()
        audiodg_session.ProcessId = 77777
        audiodg_session.Process.name.return_value = "audiodg.exe"
        assert dc._is_jarvis_or_system_process(audiodg_session) is True

        # Media app (Spotify, Chrome, Yandex) -> Must NOT be excluded
        media_session = MagicMock()
        media_session.ProcessId = 12345
        media_session.Process.name.return_value = "Spotify.exe"
        assert dc._is_jarvis_or_system_process(media_session) is False

        chrome_session = MagicMock()
        chrome_session.ProcessId = 54321
        chrome_session.Process.name.return_value = "chrome.exe"
        assert dc._is_jarvis_or_system_process(chrome_session) is False
    finally:
        dc.close()


def test_master_volume_untouched_by_default():
    """По умолчанию duck_master=False: системная общая громкость не занижается,

    чтобы голос Джарвиса из динамиков звучал на все 100%.
    """
    dc = DuckingController(duck_ratio=0.2, attack_ms=10.0)
    try:
        assert dc.duck_master is False
        mock_endpoint = MagicMock()
        dc._endpoint_volume = mock_endpoint

        dc.duck()
        time.sleep(0.05)

        # Master volume must NOT have been lowered
        mock_endpoint.SetMasterVolumeLevelScalar.assert_not_called()
    finally:
        dc.close()


def test_session_ducking_and_drift_protection():
    """Проверка плавного затухания сторонних сессий и защиты от сползания громкости."""
    dc = DuckingController(duck_ratio=0.2, attack_ms=20.0, release_ms=30.0)
    try:
        # Mocking an active Spotify session
        spotify_session = MagicMock()
        spotify_session.ProcessId = 4444
        spotify_session.Process.name.return_value = "spotify.exe"

        vol_ctl = MagicMock()
        vol_ctl.GetMasterVolume.return_value = 0.85
        spotify_session._ctl.QueryInterface.return_value = vol_ctl

        with patch("pycaw.pycaw.AudioUtilities.GetAllSessions", return_value=[spotify_session]):
            # 1. Start ducking
            dc.duck()
            assert 4444 in dc._saved_session_vols
            assert dc._saved_session_vols[4444] == pytest.approx(0.85, rel=1e-2)

            time.sleep(0.05)
            # Volume set on volume_ctl must be ~ 0.85 * 0.20 = 0.17
            last_call_val = vol_ctl.SetMasterVolume.call_args[0][0]
            assert last_call_val < 0.30

            # 2. Restore
            dc.restore()
            time.sleep(0.08)
            # Final call must restore exactly 0.85
            assert vol_ctl.SetMasterVolume.call_args[0][0] == pytest.approx(0.85, rel=1e-2)
            assert len(dc._saved_session_vols) == 0

            # 3. Multiple cycles must not drift downwards
            for _ in range(3):
                dc.duck()
                time.sleep(0.03)
                dc.restore()
                time.sleep(0.05)

            assert vol_ctl.SetMasterVolume.call_args[0][0] == pytest.approx(0.85, rel=1e-2)
    finally:
        dc.close()


def test_follow_up_window_holds_ducking_state():
    """Когда Джарвис замолчал, но активно Follow-up окно, дакинг удерживается в LISTENING."""
    ui = HeadlessUI()
    with patch("core.hotkey_manager.GlobalHotkeyManager"), \
         patch("core.wake_detector.WakeWordDetector2Stage"), \
         patch("main.Jarvis._start_telegram_bot", return_value=None):
        j = Jarvis(ui)

    with patch("core.ducking_controller.ducking_controller.set_state") as mock_set_state:
        # 1. Speaking started
        j.set_speaking(True)
        mock_set_state.assert_called_with(DuckingState.SPEAKING)

        # 2. Speaking stopped while follow-up window is active (e.g. +5.0s)
        j._wake_active_until = time.monotonic() + 5.0
        j.set_speaking(False)
        mock_set_state.assert_called_with(DuckingState.LISTENING)

        # 3. Speaking stopped when no follow-up window is open
        j._wake_active_until = 0.0
        j.set_speaking(False)
        mock_set_state.assert_called_with(DuckingState.RESTORING)
