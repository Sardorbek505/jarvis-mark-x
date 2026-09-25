"""Unit tests for actions/sleep_timer.py — Smart Sleep Timer with voice confirmation."""

from unittest.mock import patch

from actions.sleep_timer import SleepTimerManager, sleep_timer


class DummyPlayer:
    def __init__(self):
        self.logs = []
        self.spoken = []

    def write_log(self, text: str):
        self.logs.append(text)

    def speak(self, text: str):
        self.spoken.append(text)


class DummyBot:
    def __init__(self):
        self.spoken = []

    def speak(self, text: str):
        self.spoken.append(text)


def test_sleep_timer_set_and_status():
    mgr = SleepTimerManager()
    player = DummyPlayer()
    bot = DummyBot()

    msg = mgr.start_timer(30, player=player, bot=bot)
    assert "30 мин" in msg
    assert mgr.is_active() is True
    assert mgr.get_remaining_seconds() > 0

    status = mgr.get_status()
    assert "Таймер сна активен" in status
    assert "мин" in status

    # Cancel
    cancel_msg = mgr.cancel_timer(player=player)
    assert "отменён" in cancel_msg
    assert mgr.is_active() is False


def test_sleep_timer_dispatcher_function():
    player = DummyPlayer()
    
    # Set via dict
    res_set = sleep_timer({"action": "set", "duration_minutes": 15}, player=player)
    assert "15 мин" in res_set

    # Status via dict
    res_status = sleep_timer({"action": "status"}, player=player)
    assert "Таймер сна активен" in res_status

    # Cancel via dict
    res_cancel = sleep_timer({"action": "cancel"}, player=player)
    assert "отменён" in res_cancel


def test_sleep_timer_expired_and_silence_shutdown():
    mgr = SleepTimerManager()
    player = DummyPlayer()
    bot = DummyBot()

    with patch.object(mgr, "_execute_shutdown") as mock_shutdown:
        # Patch sleep/wait to run instantly
        with patch.object(mgr._cancel_event, "wait", return_value=False):
            mgr._bot_ref = bot
            mgr._on_timer_expired(player=player)

            # Check confirmation prompt was spoken
            assert any("планировали лечь спать" in s for s in bot.spoken)
            # Check shutdown was executed because silence (wait returned False)
            mock_shutdown.assert_called_once_with(player)


def test_sleep_timer_expired_and_user_cancelled():
    mgr = SleepTimerManager()
    player = DummyPlayer()
    bot = DummyBot()

    with patch.object(mgr, "_execute_shutdown") as mock_shutdown:
        # User answers "no" -> wait returns True (cancelled)
        with patch.object(mgr._cancel_event, "wait", return_value=True):
            mgr._bot_ref = bot
            mgr._on_timer_expired(player=player)

            # Confirmation prompt was spoken
            assert any("планировали лечь спать" in s for s in bot.spoken)
            # Shutdown should NOT be called
            mock_shutdown.assert_not_called()
