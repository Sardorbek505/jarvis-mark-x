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


def test_play_with_track_uri_does_not_raise(monkeypatch):
    """Путь «нашли Spotify URI» доходит до ответа, а не падает NameError."""
    monkeypatch.setattr(music_player, "_spotify_search_track_uri", lambda q: "spotify:track:xyz")
    monkeypatch.setattr(music_player, "_open_spotify_uri", lambda uri: True)
    monkeypatch.setattr(music_player, "_focus_spotify_window", lambda: True)
    monkeypatch.setattr(music_player, "_send_key", lambda key: True)
    monkeypatch.setattr(music_player, "_send_media_key", lambda action: True)
    monkeypatch.setattr(music_player.time, "sleep", lambda s: None)

    answer = music_player._play(query="Bohemian Rhapsody")
    assert "Bohemian Rhapsody" in answer


def test_youtube_fallback_reports_success_when_tab_opened(monkeypatch):
    """Промах по клавише не должен превращаться в «не удалось воспроизвести».

    Вкладка открыта и играет — значит ответ пользователю положительный.
    """
    monkeypatch.setattr(music_player, "_spotify_search_track_uri", lambda q: None)
    monkeypatch.setattr(music_player, "_is_spotify_installed", lambda: False)
    monkeypatch.setattr(music_player, "_find_youtube_direct_url", lambda q: "https://youtu.be/x")
    monkeypatch.setattr(music_player, "browser_control", lambda *a, **k: "ok")
    monkeypatch.setattr(music_player, "_send_key", lambda key: False)      # клавиша не прошла
    monkeypatch.setattr(music_player, "_send_media_key", lambda action: False)
    monkeypatch.setattr(music_player.time, "sleep", lambda s: None)

    answer = music_player._play(query="Smells Like Teen Spirit")
    assert "Не удалось" not in answer
    assert "Smells Like Teen Spirit" in answer


def test_youtube_fallback_reports_failure_when_browser_fails(monkeypatch):
    """А вот если браузер не открылся — честно сообщаем о провале."""
    def _boom(*a, **k):
        raise RuntimeError("browser is gone")

    monkeypatch.setattr(music_player, "_spotify_search_track_uri", lambda q: None)
    monkeypatch.setattr(music_player, "_is_spotify_installed", lambda: False)
    monkeypatch.setattr(music_player, "_find_youtube_direct_url", lambda q: "https://youtu.be/x")
    monkeypatch.setattr(music_player, "browser_control", _boom)
    monkeypatch.setattr(music_player.time, "sleep", lambda s: None)

    assert "Не удалось" in music_player._play(query="что угодно")


def test_playlist_with_non_spotify_url_does_not_raise(monkeypatch):
    """Не-спотифаевская ссылка на плейлист: `spotify_opened` был не определён.

    Ветка `if uri:` не выполнялась, и следующая строка читала переменную,
    которой ещё нет, — UnboundLocalError вместо открытия ссылки в браузере.
    """
    monkeypatch.setattr(music_player, "_https_to_spotify_uri", lambda url: None)
    monkeypatch.setattr(music_player, "browser_control", lambda *a, **k: "ok")
    monkeypatch.setattr(music_player, "_focus_spotify_window", lambda: True)
    monkeypatch.setattr(music_player, "_send_media_key", lambda action: True)
    monkeypatch.setattr(music_player.time, "sleep", lambda s: None)

    answer = music_player._play(playlist_url="https://music.yandex.ru/users/x/playlists/1")
    assert "Не удалось" not in answer
