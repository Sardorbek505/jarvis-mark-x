"""Тесты для менеджера медиа-сессий Windows GSMTC (Что сейчас играет)."""

import pytest
from unittest.mock import patch
from core.media_session_manager import MediaSessionManager, _clean_app_name
from core.fast_command_router import FastCommandRouter
from actions.music_player import music_player


def test_clean_app_name():
    assert _clean_app_name("Spotify.exe") == "Spotify"
    assert _clean_app_name("chrome.exe") == "Chrome"
    assert _clean_app_name("msedge.exe") == "Edge"
    assert _clean_app_name("C:\\Program Files\\WindowsApps\\SpotifyAB.SpotifyMusic_123\\Spotify.exe") == "Spotify"
    assert _clean_app_name("YandexMusic.exe") == "Яндекс Музыке"
    assert _clean_app_name("") == ""


def test_now_playing_speech_playing():
    mock_info = {
        "active": True,
        "title": "In the End",
        "artist": "Linkin Park",
        "album": "Hybrid Theory",
        "status": "playing",
        "app_name": "Spotify",
    }
    with patch.object(MediaSessionManager, "get_current_media_info_sync", return_value=mock_info):
        speech = MediaSessionManager.get_now_playing_speech()
        assert "Linkin Park" in speech
        assert "In the End" in speech
        assert "Spotify" in speech
        assert "Сейчас играет" in speech


def test_now_playing_speech_paused():
    mock_info = {
        "active": True,
        "title": "Numb",
        "artist": "Linkin Park",
        "album": "Meteora",
        "status": "paused",
        "app_name": "Chrome",
    }
    with patch.object(MediaSessionManager, "get_current_media_info_sync", return_value=mock_info):
        speech = MediaSessionManager.get_now_playing_speech()
        assert "На паузе стоит" in speech
        assert "Linkin Park" in speech
        assert "Chrome" in speech


def test_now_playing_speech_empty():
    mock_info = {"active": False, "reason": "no_active_session"}
    with patch.object(MediaSessionManager, "get_current_media_info_sync", return_value=mock_info):
        speech = MediaSessionManager.get_now_playing_speech()
        assert "ничего не воспроизводится" in speech


def test_fast_router_what_is_playing():
    mock_speech = "Сейчас играет Queen — «Bohemian Rhapsody» в Spotify, сэр."
    with patch.object(MediaSessionManager, "get_now_playing_speech", return_value=mock_speech):
        handled, resp = FastCommandRouter.match_and_execute("Джарвис, что сейчас играет?")
        assert handled is True
        assert resp == mock_speech

        handled, resp = FastCommandRouter.match_and_execute("какой трек играет")
        assert handled is True
        assert resp == mock_speech

        handled, resp = FastCommandRouter.match_and_execute("кто поет")
        assert handled is True
        assert resp == mock_speech


def test_music_player_now_playing_action():
    mock_speech = "Сейчас играет Hans Zimmer — «Time» в Spotify, сэр."
    with patch.object(MediaSessionManager, "get_now_playing_speech", return_value=mock_speech):
        resp = music_player({"action": "now_playing"})
        assert resp == mock_speech
