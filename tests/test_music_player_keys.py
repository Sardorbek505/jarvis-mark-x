"""Основной сценарий запуска музыки не должен падать на отправке клавиши.

Коммит 0f4a136 «Fix music playback multi-layer fallback, Spotify URI auto-trigger
and YouTube instant autoplay» добавил в actions/music_player.py вызовы
`_send_key("space")`, но само определение осталось в actions/movie_player.py.

Что это давало вживую:
  * найден Spotify Track URI — NameError улетал наружу из _play(), то есть
    основной путь «включи <трек>» падал целиком;
  * откат на YouTube — NameError гасился `except Exception` и Джарвис отвечал
    «Не удалось воспроизвести трек», хотя вкладка уже открылась и играла.

Тесты pytest этого не видели: пути дёргают Spotify и браузер и не покрыты.
Ловит такое линтер (ruff F821), который теперь стоит в CI отдельным шагом.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actions.music_player as music_player
from actions.keyboard import send_key


def test_send_key_is_defined_in_music_player():
    """Имя, которое модуль вызывает, должно существовать и быть вызываемым."""
    assert callable(getattr(music_player, "_send_key", None))


def test_music_and_movie_players_share_one_implementation():
    """Обе реализации — одна функция, чтобы правки не расходились."""
    import actions.movie_player as movie_player

    assert music_player._send_key is send_key
    assert movie_player._send_key is send_key


def test_send_key_rejects_unknown_key_without_raising():
    assert send_key("no-such-key") is False


def _fake_orchestrator(monkeypatch, answer="Включаю «X», сэр."):
    calls = []

    class _Orch:
        def play_media(self, raw_query, parameters=None):
            calls.append((raw_query, parameters))
            return answer

    monkeypatch.setattr(music_player, "get_media_orchestrator", lambda: _Orch())
    return calls


def test_play_passes_query_as_string_and_marks_it_music(monkeypatch):
    """play_media ждёт СТРОКУ: объект MediaRequest там разбирался заново и
    становился фильмом — музыка уходила искаться на VK Видео (стенд 10.09.2026)."""
    calls = _fake_orchestrator(monkeypatch)

    music_player.music_player({"action": "play", "query": "Bohemian Rhapsody"})

    assert len(calls) == 1
    raw_query, parameters = calls[0]
    assert raw_query == "Bohemian Rhapsody"
    assert parameters["media_type"] == "music"


def test_legacy_play_uses_the_same_orchestrator_path(monkeypatch):
    calls = _fake_orchestrator(monkeypatch)

    answer = music_player._play(query="Smells Like Teen Spirit")

    assert "Не удалось" not in answer
    assert calls[0][0] == "Smells Like Teen Spirit"
    assert calls[0][1]["media_type"] == "music"


def test_legacy_play_reports_failure_when_orchestrator_raises(monkeypatch):
    class _Orch:
        def play_media(self, raw_query, parameters=None):
            raise RuntimeError("browser is gone")

    monkeypatch.setattr(music_player, "get_media_orchestrator", lambda: _Orch())

    assert "Не удалось" in music_player._play(query="что угодно")


def test_spotify_helpers_are_real_functions_not_stubs():
    """Провайдер Spotify импортирует хелперы отсюда; лямбды-заглушки «для тестов»
    заставляли его рапортовать «Воспроизведение запущено», ничего не включив."""
    import actions.spotify_desktop as sd

    for name in ("_spotify_search_track_uri", "_open_spotify_uri", "_focus_spotify_window",
                 "_https_to_spotify_uri", "_ui_automation_search", "_send_media_key",
                 "_is_spotify_installed", "_is_spotify_running", "_find_youtube_direct_url"):
        assert getattr(music_player, name) is getattr(sd, name)
        assert getattr(sd, name).__name__ != "<lambda>"

    assert sd._https_to_spotify_uri("https://open.spotify.com/track/abc?si=1") == "spotify:track:abc"
    assert sd._open_spotify_uri("evil; rm -rf /") is False
