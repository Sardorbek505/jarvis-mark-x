import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from telegram_bot.pc_server import _read_unlock_password, _do_unlock, _do_screenshot, _execute
from actions.computer_settings import computer_settings
from actions.open_app import open_app


class TestPcServerAndSystemFixes(unittest.TestCase):
    def test_unlock_password_sync(self):
        from actions.claude_terminal import save_unlock_password, _UNLOCK_FILE
        test_pwd = "SyncPass777"
        try:
            save_unlock_password(test_pwd)
            self.assertEqual(_read_unlock_password(), test_pwd)
        finally:
            if _UNLOCK_FILE.exists():
                _UNLOCK_FILE.unlink()

    @patch("telegram_bot.pc_server._press_vk")
    @patch("telegram_bot.pc_server._type_char")
    def test_do_unlock_no_password_does_not_type_gibberish(self, mock_type, mock_press):
        with patch("telegram_bot.pc_server._read_unlock_password", return_value=""):
            res = _do_unlock()
            mock_type.assert_not_called()
            self.assertIn("сигнал пробуждения", res["text"])

    @patch("telegram_bot.pc_server._press_vk")
    @patch("telegram_bot.pc_server._type_char")
    def test_do_unlock_with_password(self, mock_type, mock_press):
        with patch("telegram_bot.pc_server._read_unlock_password", return_value="1234"):
            res = _do_unlock()
            self.assertEqual(mock_type.call_count, 4)
            self.assertIn("пробел → пароль → Enter", res["text"])

    @patch("actions.vision.capture_screen_jpeg", return_value=b"test_jpeg_bytes")
    def test_do_screenshot_in_ram(self, mock_capture):
        res = asyncio.run(_do_screenshot())
        self.assertEqual(res.get("text"), "Скриншот ✅")
        self.assertIsNotNone(res.get("image_b64"))

    def test_do_screenshot_fallback_cleans_up_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(b"dummy_png")
            tmp_path = tmp.name

        try:
            with patch("actions.vision.capture_screen_jpeg", side_effect=Exception("RAM fail")), \
                 patch("telegram_bot.pc_server._encode_image", return_value="fake_b64"), \
                 patch("actions.computer_settings.computer_settings", return_value=f"Скриншот сохранён: {tmp_path}"):
                res = asyncio.run(_do_screenshot())
                self.assertEqual(res.get("text"), "Скриншот ✅")
                self.assertFalse(os.path.exists(tmp_path), "Fallback file should have been cleaned up")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch("subprocess.Popen")
    def test_computer_settings_sleep(self, mock_popen):
        res = computer_settings({"action": "сон"})
        self.assertIn("спящий режим", res)
        mock_popen.assert_called_once()

    @patch("subprocess.Popen")
    def test_pc_server_execute_sleep(self, mock_popen):
        res = asyncio.run(_execute("усыпи пк"))
        self.assertIn("спящий режим", res.get("text", ""))

    @patch("os.startfile")
    def test_open_app_uri_protocol(self, mock_startfile):
        res = open_app({"app_name": "настройки"})
        self.assertIn("Открыл настройки", res)
        mock_startfile.assert_called_once_with("ms-settings:")

    @patch("os.startfile")
    def test_open_app_direct_path_quoted(self, mock_startfile):
        # Путь обязан существовать: open_app запускает файл напрямую только
        # после os.path.exists. Хардкод чужого рабочего стола проходил лишь на
        # машине, где этот ярлык лежал, и падал у всех остальных.
        with tempfile.TemporaryDirectory() as tmp:
            test_path = os.path.join(tmp, "Counter-Strike 2.url")
            with open(test_path, "w", encoding="utf-8") as fh:
                fh.write("[InternetShortcut]\nURL=steam://rungameid/730\n")

            res = open_app({"app_name": f'"{test_path}"'})

            self.assertTrue("Открыл" in res or "Запущено" in res, res)
            mock_startfile.assert_called_once()
            self.assertEqual(mock_startfile.call_args[0][0], test_path)

    @patch("os.startfile")
    def test_open_app_game_aliases(self, mock_startfile):
        for alias in ["cs2", "кс", "кс 2", "контра", "counter-strike 2"]:
            mock_startfile.reset_mock()
            res = open_app({"app_name": alias})
            self.assertIn("Открыл", res)
            mock_startfile.assert_called_once()
            self.assertTrue(mock_startfile.call_args[0][0].endswith("Counter-Strike 2.url"))

    @patch("os.startfile")
    def test_open_app_desktop_shortcuts(self, mock_startfile):
        for app in ["Obsidian", "Canva", "Framer"]:
            mock_startfile.reset_mock()
            res = open_app({"app_name": app})
            self.assertIn("Открыл", res)
            mock_startfile.assert_called_once()

    def test_slot_filler_open_app_paths_and_quotes(self):
        from core.slot_filler import SlotFiller
        # Проверка пути в кавычках
        intent, slots = SlotFiller.detect_new_intent(r'открой "C:\Users\User\Desktop\Counter-Strike 2.url"')
        self.assertEqual(intent, "open_app")
        self.assertEqual(slots.get("app_name").lower(), r"C:\Users\User\Desktop\Counter-Strike 2.url".lower())

        # Проверка прямого пути без кавычек
        intent, slots = SlotFiller.detect_new_intent(r"запусти C:\Users\User\Desktop\Counter-Strike 2.url")
        self.assertEqual(intent, "open_app")
        self.assertEqual(slots.get("app_name").lower(), r"C:\Users\User\Desktop\Counter-Strike 2.url".lower())

        # Проверка алиаса игры
        intent, slots = SlotFiller.detect_new_intent("открой кс 2")
        self.assertEqual(intent, "open_app")
        self.assertEqual(slots.get("app_name"), "кс 2")


if __name__ == '__main__':
    unittest.main()

