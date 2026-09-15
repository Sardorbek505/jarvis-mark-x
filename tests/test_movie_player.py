"""Unit tests for actions/movie_player.py.

Оркестратор подменяется «без активной сессии»: иначе тест зависел от сессии,
оставленной другими тестами в глобальном трекере, и проверял чужой фейк.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.movie_player import movie_player  # noqa: E402


@pytest.fixture(autouse=True)
def no_active_session():
    orch = MagicMock()
    orch.enter_fullscreen.return_value = "Нет активного плеера для полного экрана, сэр."
    orch.seek_percent.return_value = "Нет активного фильма для перемотки, сэр."
    with patch("actions.movie_player.get_media_orchestrator", return_value=orch):
        yield orch


def test_movie_player_play_empty_title():
    res = movie_player({"action": "play", "title": ""})
    assert "Назовите фильм" in res


def test_movie_player_seek_forward():
    with patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player.pyautogui.press") as mock_press:
        res = movie_player({"action": "seek_forward", "minutes": 5})
        assert "5 мин." in res
        # 5 минут = 300 сек. В плеере 5 сек на шаг = 60 нажатий
        assert mock_press.call_count == 60


def test_movie_player_seek_back():
    with patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player.pyautogui.press") as mock_press:
        res = movie_player({"action": "seek_back", "seconds": 30})
        assert "30 сек." in res
        # 30 сек = 6 нажатий
        assert mock_press.call_count == 6


def test_movie_player_seek_position():
    with patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player.pyautogui.click") as mock_click, \
         patch("actions.movie_player.pyautogui.size", return_value=(1920, 1080)):
        res = movie_player({"action": "seek_to", "position": "середина"})
        assert "50%" in res
        assert mock_click.called


def test_movie_player_fullscreen():
    with patch("actions.movie_player._focus_movie_player", return_value=True), \
         patch("actions.movie_player._send_key", return_value=True) as mock_key:
        res = movie_player({"action": "fullscreen"})
        assert "полный экран" in res.lower()
        mock_key.assert_called_once_with("f")


def test_seek_without_video_window_says_so_honestly():
    """Нет окна с видео — не «перемотал», а честный отказ, и клавиши не жмутся."""
    with patch("actions.movie_player._focus_movie_player", return_value=False), \
         patch("actions.movie_player.pyautogui.press") as mock_press:
        res = movie_player({"action": "seek_forward", "seconds": 30})
    assert "Не нашёл окно" in res
    mock_press.assert_not_called()


def test_fullscreen_without_video_window_does_not_press_f():
    """Раньше «F» уходила в любое активное окно (IDE, мессенджер)."""
    with patch("actions.movie_player._focus_movie_player", return_value=False), \
         patch("actions.movie_player._send_key") as mock_key:
        res = movie_player({"action": "fullscreen"})
    assert "Не нашёл окно" in res
    mock_key.assert_not_called()

