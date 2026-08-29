"""Тесты для модуля голосовой отправки в Telegram."""
from unittest.mock import MagicMock, patch
import pytest

from actions import telegram_sender


def test_telegram_sender_missing_config():
    with patch("actions.telegram_sender._get_tg_config", return_value=("", [])):
        res = telegram_sender.send_to_telegram("Привет")
        assert "не настроена" in res.lower()


def test_telegram_sender_success():
    with patch("actions.telegram_sender._get_tg_config", return_value=("dummy_token", [123456789])):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = MagicMock()
            res = telegram_sender.send_to_telegram("Тестовое сообщение")
            assert "успешно отправлено" in res.lower()
