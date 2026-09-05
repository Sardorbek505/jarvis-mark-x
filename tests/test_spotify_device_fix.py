"""Unit tests verifying device type matching and empty query handling."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.spotify.devices import SpotifyDevices
from tools.spotify.controller import SpotifyController
from actions.spotify_controller import spotify_player


def test_get_desktop_device_matches_computer():
    """Spotify API отдаёт type: Computer (не desktop). Проверяем распознавание."""
    dev = SpotifyDevices("dummy_token")
    fake_devices = [
        {"id": "phone1", "name": "iPhone", "type": "Smartphone"},
        {"id": "pc1", "name": "SARDORBEK", "type": "Computer"},
    ]
    with patch.object(dev, "list_devices", return_value=fake_devices):
        desktop = dev.get_desktop_device()
        assert desktop is not None
        assert desktop["id"] == "pc1"
        assert desktop["name"] == "SARDORBEK"


def test_get_desktop_device_fallback_first():
    """Если явного типа нет, берётся первое доступное устройство."""
    dev = SpotifyDevices("dummy_token")
    fake_devices = [{"id": "spk1", "name": "SmartSpeaker", "type": "Speaker"}]
    with patch.object(dev, "list_devices", return_value=fake_devices):
        desktop = dev.get_desktop_device()
        assert desktop is not None
        assert desktop["id"] == "spk1"


def test_play_query_empty_resumes_or_plays_top_playlist():
    c = SpotifyController.__new__(SpotifyController)
    c.track_cache = {}
    c.last_query = c.last_uri = None
    c._refresh_components = lambda: True
    c.resume = lambda: "Продолжаю, сэр."
    c.play_context = lambda uri: True

    res = c.play_query("")
    assert "Продолжаю" in res

    c.resume = lambda: "Не удалось"
    res2 = c.play_query("")
    assert "любимую музыку" in res2


def test_spotify_player_empty_query_delegation():
    with patch("actions.spotify_controller.spotify_api") as mock_api:
        mock_api.is_ready.return_value = True
        mock_api.resume.return_value = "Продолжаю, сэр."
        res = spotify_player({"action": "play"})
        assert "Продолжаю" in res
        mock_api.resume.assert_called_once()

