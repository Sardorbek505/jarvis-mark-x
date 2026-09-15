"""End-to-end lifecycle and deterministic state machine transition tests."""

import time
from core.conversation_state import ConversationState, ConversationStateMachine


def test_standard_conversation_lifecycle():
    """Проверка стандартного цикла диалога:
    STANDBY -> LISTENING -> THINKING -> EXECUTING -> SPEAKING -> FOLLOW_UP -> STANDBY
    """
    history = []
    sm = ConversationStateMachine(initial_state=ConversationState.STANDBY)
    sm.subscribe(lambda old, new, reason: history.append((old, new, reason)))

    assert sm.state == ConversationState.STANDBY
    assert sm.is_active is False

    # 1. Wake word spotted / F8 hotkey -> LISTENING
    assert sm.transition_to(ConversationState.LISTENING, reason="wake word spotted") is True
    assert sm.state == ConversationState.LISTENING
    assert sm.is_listening is True
    assert sm.is_active is True

    # 2. End of speech / processing query -> THINKING
    assert sm.transition_to(ConversationState.THINKING, reason="user speech completed") is True
    assert sm.state == ConversationState.THINKING
    assert sm.is_listening is False

    # 3. Tool call / Macro execution -> EXECUTING
    assert sm.transition_to(ConversationState.EXECUTING, reason="calling weather tool") is True
    assert sm.state == ConversationState.EXECUTING

    # 4. Speaking answer -> SPEAKING
    assert sm.transition_to(ConversationState.SPEAKING, reason="playback started") is True
    assert sm.state == ConversationState.SPEAKING
    assert sm.is_speaking is True

    # 5. Playback completes -> FOLLOW_UP with timer
    sm.start_follow_up(timeout_sec=0.2, on_timeout_reason="follow-up expired")
    assert sm.state == ConversationState.FOLLOW_UP
    assert sm.is_listening is True

    # 6. Wait for timer expiration -> STANDBY
    time.sleep(0.35)
    assert sm.state == ConversationState.STANDBY
    assert sm.is_active is False

    # Проверяем точную последовательность переходов
    states_sequence = [h[1] for h in history]
    assert states_sequence == [
        ConversationState.LISTENING,
        ConversationState.THINKING,
        ConversationState.EXECUTING,
        ConversationState.SPEAKING,
        ConversationState.FOLLOW_UP,
        ConversationState.STANDBY,
    ]


def test_barge_in_interruption_lifecycle():
    """Проверка жизненного цикла при перебивании (Barge-In):
    SPEAKING -> INTERRUPTED -> LISTENING
    """
    history = []
    sm = ConversationStateMachine(initial_state=ConversationState.SPEAKING)
    sm.subscribe(lambda old, new, reason: history.append((old, new, reason)))

    assert sm.state == ConversationState.SPEAKING
    assert sm.is_speaking is True

    # Пользователь перебивает речь (RMS или WakeWord)
    assert sm.transition_to(ConversationState.INTERRUPTED, reason="barge-in detected") is True
    assert sm.state == ConversationState.INTERRUPTED
    assert sm.is_speaking is False

    # Немедленный переход к прослушиванию новой команды
    assert sm.transition_to(ConversationState.LISTENING, reason="ready for new query") is True
    assert sm.state == ConversationState.LISTENING
    assert sm.is_listening is True

    states_sequence = [h[1] for h in history]
    assert states_sequence == [
        ConversationState.INTERRUPTED,
        ConversationState.LISTENING,
    ]


def test_illegal_transitions_rejected():
    """Машина состояний должна отклонять недопустимые переходы."""
    sm = ConversationStateMachine(initial_state=ConversationState.STANDBY)

    # Из STANDBY нельзя напрямую прыгнуть в SPEAKING или EXECUTING
    assert sm.transition_to(ConversationState.SPEAKING) is False
    assert sm.state == ConversationState.STANDBY

    assert sm.transition_to(ConversationState.EXECUTING) is False
    assert sm.state == ConversationState.STANDBY

    # Но force=True разрешает переход в исключительных аварийных ситуациях
    assert sm.transition_to(ConversationState.ERROR, force=True) is True
    assert sm.state == ConversationState.ERROR


def test_reconnecting_resilience_transition():
    """Переход в RECONNECTING при сбое устройства и возврат в STANDBY при восстановлении."""
    sm = ConversationStateMachine(initial_state=ConversationState.STANDBY)

    assert sm.transition_to(ConversationState.RECONNECTING, reason="mic unplugged") is True
    assert sm.state == ConversationState.RECONNECTING

    assert sm.transition_to(ConversationState.STANDBY, reason="mic re-opened") is True
    assert sm.state == ConversationState.STANDBY
