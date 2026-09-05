"""Tests for strict wake-word gating."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import is_addressed_to_jarvis  # noqa: E402


def test_addressed_to_jarvis_positive():
    assert is_addressed_to_jarvis("Джарвис, открой браузер") is True
    assert is_addressed_to_jarvis("джарвис какая погода") is True
    assert is_addressed_to_jarvis("эй джарвис включи музыку") is True
    assert is_addressed_to_jarvis("слушай джарвис сделай потише") is True
    assert is_addressed_to_jarvis("привет джарвис") is True
    assert is_addressed_to_jarvis("Jarvis open Spotify") is True
    assert is_addressed_to_jarvis("Джарвис") is True


def test_addressed_to_jarvis_negative_room_conversation():
    assert is_addressed_to_jarvis("открой браузер") is False
    assert is_addressed_to_jarvis("какая погода") is False
    assert is_addressed_to_jarvis("включи музыку") is False
    assert is_addressed_to_jarvis("сделай потише") is False
    assert is_addressed_to_jarvis("привет") is False
    assert is_addressed_to_jarvis("давай посмотрим фильм") is False
    assert is_addressed_to_jarvis("где мой телефон") is False
    assert is_addressed_to_jarvis("я пошел на кухню") is False
    assert is_addressed_to_jarvis("ты тут") is False

