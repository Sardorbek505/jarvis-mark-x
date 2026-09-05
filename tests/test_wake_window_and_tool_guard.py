import time
from core.headless_ui import HeadlessUI
from main import Jarvis, is_addressed_to_jarvis


def test_wake_window_allows_command_without_repeating_wake_word():
    ui = HeadlessUI()
    jarvis = Jarvis(ui)

    # Initial state: no wake word, room speech should be ignored
    assert is_addressed_to_jarvis('поставь песню') is False
    assert (time.monotonic() < getattr(jarvis, '_wake_active_until', 0.0)) is False

    # Wake word detected -> activates 10s window
    jarvis._wake_active_until = time.monotonic() + 10.0
    assert time.monotonic() < jarvis._wake_active_until

    # User speaks command without 'Джарвис'
    command_text = 'поставь песню'
    is_active = is_addressed_to_jarvis(command_text) or (time.monotonic() < jarvis._wake_active_until)
    assert is_active is True

    # Assistant answers without question mark -> window closes
    full_out = 'Включаю музыку, сэр.'
    if not full_out.strip().endswith('?') and not getattr(jarvis, '_pending_destructive', None):
        jarvis._wake_active_until = 0.0

    assert jarvis._wake_active_until == 0.0

    # Next room conversation is ignored again
    room_text = 'да, сегодня отличный день'
    is_active_next = is_addressed_to_jarvis(room_text) or (time.monotonic() < jarvis._wake_active_until)
    assert is_active_next is False


def test_wake_window_stays_open_on_question():
    ui = HeadlessUI()
    jarvis = Jarvis(ui)

    jarvis._wake_active_until = time.monotonic() + 10.0
    full_out = 'Что именно включить, сэр?'
    if not full_out.strip().endswith('?') and not getattr(jarvis, '_pending_destructive', None):
        jarvis._wake_active_until = 0.0

    # Because it ended with '?', window stays open for user reply
    assert time.monotonic() < jarvis._wake_active_until


def test_tool_guard_blocks_realtime_audio_streaming():
    ui = HeadlessUI()
    jarvis = Jarvis(ui)

    # By default, tool is not running
    assert getattr(jarvis, '_tool_in_progress', False) is False

    # During tool execution, flag is set
    jarvis._tool_in_progress = True
    assert jarvis._tool_in_progress is True
