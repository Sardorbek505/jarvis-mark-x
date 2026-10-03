"""Фильмы и сериалы из Telegram не уходят в музыку.

Живой случай владельца: голосом «поставь сериал Локи…» — ПК включил трек
«Один на один — JABO» (сработало «поставь» в ветке музыки); «я просил сериал,
а не музыку» — «Продолжаю музыку»; «отключи музыку и поставь сериал Локи,
первый сезон, вторую серию» — только «Пауза»."""
import asyncio

import pytest

from telegram_bot import pc_server as pc

CASES = [
    ("поставь сериал Локи первый сезон вторая серия", ("Локи", 1, 2)),
    ("Нет я просил тебе поставить сериал то есть фильм а не музыку давай отключи музыку и "
     "поставь этот фильм то есть сериал называется Локи ты должен поставить первый сезон "
     "вторую серию", ("Локи", 1, 2)),
    ("Я просил сериал помтавить а не музыку", ("", None, None)),
    ("поставь Локи 1 сезон 2 серия", ("Локи", 1, 2)),
    ("включи Локи сезон 1 серия 3", ("Локи", 1, 3)),
    ("включи третью серию Локи", ("Локи", None, 3)),
    ("включи фильм Интерстеллар", ("Интерстеллар", None, None)),
    ("поставь фильм Один дома 2", ("Один дома 2", None, None)),
    ("поставь сериал Во все тяжкие", ("Во все тяжкие", None, None)),
    ("включи кино", ("", None, None)),
]


@pytest.mark.parametrize("text, expected", CASES)
def test_parse_video(text, expected):
    assert pc._parse_video(text) == expected


@pytest.mark.parametrize("text", ["включи музыку", "поставь трек Believer", "открой кинопоиск",
                                  "следующий трек", "пауза"])
def test_not_video(text):
    assert pc._parse_video(text) is None


@pytest.fixture
def calls(monkeypatch):
    got = {"movie": [], "music": [], "media": []}
    from actions import movie_player, music_player
    from core import media_session
    monkeypatch.setattr(movie_player, "movie_player", lambda p, player=None: got["movie"].append(p) or "Включил.")
    monkeypatch.setattr(music_player, "music_player", lambda p, player=None: got["music"].append(p) or "Музыка.")
    monkeypatch.setattr(media_session, "command", lambda cmd, app=None: got["media"].append((cmd, app)) or True)
    from telegram_bot import pc_macros
    monkeypatch.setattr(pc_macros, "match_text", lambda t: None)
    return got


def test_series_goes_to_movie_player_and_music_pauses(calls):
    res = asyncio.run(pc._execute(CASES[1][0]))
    assert calls["music"] == []
    assert calls["movie"] == [{"action": "play", "title": "Локи 1 сезон 2 серия"}]
    assert ("pause", "spotify") in calls["media"]
    assert res["text"] == "Включил."


def test_complaint_without_title_asks_instead_of_playing_music(calls):
    res = asyncio.run(pc._execute("Я просил сериал помтавить а не музыку"))
    assert calls["music"] == [] and calls["movie"] == []
    assert "Какой фильм или сериал" in res["text"]


def test_music_still_works(calls):
    asyncio.run(pc._execute("включи музыку"))
    assert calls["music"] and calls["movie"] == []
