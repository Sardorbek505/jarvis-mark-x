"""Unit tests for ConversationStateMachine."""

import time
from core.conversation_state import ConversationState, ConversationStateMachine


def test_initial_state():
    sm = ConversationStateMachine()
    assert sm.state == ConversationState.STANDBY
    assert not sm.is_speaking
    assert not sm.is_listening
    assert not sm.is_active


def test_valid_lifecycle_transitions():
    sm = ConversationStateMachine()
    history = []
    sm.subscribe(lambda old, new, reason: history.append((old, new, reason)))

    # STANDBY -> LISTENING
    assert sm.transition_to(ConversationState.LISTENING, reason="wake word")
    assert sm.state == ConversationState.LISTENING
    assert sm.is_listening
    assert sm.is_active

    # LISTENING -> THINKING
    assert sm.transition_to(ConversationState.THINKING, reason="user speech end")
    assert sm.state == ConversationState.THINKING
    assert not sm.is_listening

    # THINKING -> EXECUTING
    assert sm.transition_to(ConversationState.EXECUTING, reason="tool called")
    assert sm.state == ConversationState.EXECUTING

    # EXECUTING -> SPEAKING
    assert sm.transition_to(ConversationState.SPEAKING, reason="playback started")
    assert sm.state == ConversationState.SPEAKING
    assert sm.is_speaking

    # SPEAKING -> FOLLOW_UP
    assert sm.transition_to(ConversationState.FOLLOW_UP, reason="playback ended")
    assert sm.state == ConversationState.FOLLOW_UP
    assert sm.is_listening

    # FOLLOW_UP -> STANDBY
    assert sm.transition_to(ConversationState.STANDBY, reason="timeout")
    assert sm.state == ConversationState.STANDBY

    assert len(history) == 6
    assert history[0] == (ConversationState.STANDBY, ConversationState.LISTENING, "wake word")
    assert history[-1] == (ConversationState.FOLLOW_UP, ConversationState.STANDBY, "timeout")


def test_interruption_barge_in():
    sm = ConversationStateMachine(initial_state=ConversationState.SPEAKING)
    assert sm.is_speaking

    # User interrupts speech: SPEAKING -> INTERRUPTED
    assert sm.transition_to(ConversationState.INTERRUPTED, reason="barge-in")
    assert sm.state == ConversationState.INTERRUPTED
    assert not sm.is_speaking

    # Promptly transitions to LISTENING to capture interrupting speech
    assert sm.transition_to(ConversationState.LISTENING, reason="listen to interruption")
    assert sm.state == ConversationState.LISTENING
    assert sm.is_listening


def test_invalid_transition_rejected():
    sm = ConversationStateMachine(initial_state=ConversationState.STANDBY)
    # Direct transition STANDBY -> SPEAKING is invalid without force
    assert not sm.transition_to(ConversationState.SPEAKING, reason="invalid")
    assert sm.state == ConversationState.STANDBY


def test_follow_up_timer():
    sm = ConversationStateMachine(initial_state=ConversationState.SPEAKING)
    sm.start_follow_up(timeout_sec=0.1, on_timeout_reason="test timeout")
    assert sm.state == ConversationState.FOLLOW_UP
    time.sleep(0.18)
    assert sm.state == ConversationState.STANDBY


def test_follow_up_cancelled_on_speech():
    sm = ConversationStateMachine(initial_state=ConversationState.SPEAKING)
    sm.start_follow_up(timeout_sec=0.2, on_timeout_reason="test timeout")
    assert sm.state == ConversationState.FOLLOW_UP

    # User speaks during follow-up
    assert sm.transition_to(ConversationState.LISTENING, reason="user speech detected")
    time.sleep(0.25)
    # State should remain LISTENING, not revert to STANDBY
    assert sm.state == ConversationState.LISTENING
