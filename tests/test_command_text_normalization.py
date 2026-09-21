"""Нормализация голосовой команды: имя, кавычки, концевая пунктуация.

Расшифровка Gemini берёт названия в кавычки, и они уезжали внутрь названия:
в MediaOrchestrator уходило «"железный человек "» вместо «железный человек»
(живой прогон 17.09.2026).
"""

import pytest

from core.fast_command_router import normalize_command_text


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ('Джарвис, поставь фильм "Железный человек"', "поставь фильм железный человек"),
        ("Джарвис, включи песню «Люби меня»", "включи песню люби меня"),
        ("Джарвис, включи „Мстители“", "включи мстители"),
        ("Джарвис, поставь “Интерстеллар”", "поставь интерстеллар"),
    ],
)
def test_quotes_never_reach_the_title(spoken, expected):
    assert normalize_command_text(spoken) == expected


def test_apostrophe_survives():
    """Апостроф — часть английских названий, его трогать нельзя."""
    assert normalize_command_text("Джарвис, включи Don't Stop Me Now") == "включи don't stop me now"


def test_trailing_punctuation_and_name_are_stripped():
    assert normalize_command_text("Джарвис, сделай громче!") == "сделай громче"


def test_empty_input_is_safe():
    assert normalize_command_text("") == ""
    assert normalize_command_text(None) == ""
