"""Tests for speech text normalization and LogWidget chat rendering."""

from main import _clean_dialog_text


def test_clean_dialog_text_empty():
    assert _clean_dialog_text("") == ""
    assert _clean_dialog_text(None) == ""


def test_clean_dialog_text_spaces_and_control_chars():
    raw = "\x00\x05привет,   как    дела?\x1f\n"
    assert _clean_dialog_text(raw) == "привет, как дела?"


def test_clean_dialog_text_speech_fillers():
    raw = "э-э поставь мне музыку м-м-м люби меня"
    assert _clean_dialog_text(raw) == "поставь мне музыку люби меня"


def test_clean_dialog_text_word_repair():
    raw = "став ь мне песню"
    assert _clean_dialog_text(raw) == "ставь мне песню"


def test_clean_dialog_text_fragment_assembly():
    fragments = ["став", "ь ", "мне ", "му", "зыку ", "MiyaGi"]
    assembled = _clean_dialog_text("".join(fragments))
    assert assembled == "ставь мне музыку MiyaGi"
