import unittest
from unittest.mock import patch, MagicMock

from actions.claude_terminal import (
    read_unlock_password,
    save_unlock_password,
    find_terminal_window,
    execute_claude_typing,
    _UNLOCK_FILE,
)


class TestClaudeTerminal(unittest.TestCase):
    def test_save_and_read_password(self):
        test_pwd = "SecretPass123_test"
        try:
            ok = save_unlock_password(test_pwd)
            self.assertTrue(ok)
            read_back = read_unlock_password()
            self.assertEqual(read_back, test_pwd)
        finally:
            if _UNLOCK_FILE.exists():
                _UNLOCK_FILE.unlink()

    def test_find_terminal_window_with_mock(self):
        mock_win1 = MagicMock()
        mock_win1.title = "✳ Smart Store phase 0 skeleton"
        mock_win1._hWnd = 12345

        mock_win2 = MagicMock()
        mock_win2.title = "Google Chrome"
        mock_win2._hWnd = 67890

        with patch("pygetwindow.getAllWindows", return_value=[mock_win2, mock_win1]):
            win = find_terminal_window()
            self.assertIsNotNone(win)
            self.assertEqual(win._hWnd, 12345)

    def test_execute_claude_typing_empty_text(self):
        res = execute_claude_typing("")
        self.assertIn("Не указан текст", res["text"])

    @patch("actions.claude_terminal.is_screen_locked", return_value=False)
    @patch("actions.claude_terminal.find_terminal_window")
    @patch("actions.claude_terminal.force_focus_window", return_value=True)
    @patch("actions.claude_terminal.type_text_to_terminal", return_value=True)
    @patch("actions.claude_terminal.capture_screenshot_bytes", return_value=b"fake_jpeg")
    @patch("actions.claude_terminal.verify_screen_with_eyes", return_value="Терминал готов к приёму команд")
    def test_execute_claude_typing_success(
        self, mock_eyes, mock_capture, mock_type, mock_focus, mock_find, mock_lock
    ):
        mock_win = MagicMock()
        mock_win.title = "Smart Store phase 0"
        mock_win._hWnd = 11111
        mock_find.return_value = mock_win

        res = execute_claude_typing("продолжай работу")
        self.assertEqual(res.get("status"), "success")
        self.assertIn("Smart Store phase 0", res["text"])
        self.assertIn("продолжай работу", res["text"])
        self.assertIn("Терминал готов", res["text"])
        self.assertIsNotNone(res.get("image_b64"))


if __name__ == "__main__":
    unittest.main()
