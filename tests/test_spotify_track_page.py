"""Выбор кнопки «Слушать» на странице трека Spotify.

Media-клавиша play не включает открытый `spotify:track:ID` — она продолжает
прошлый контекст, то есть плейлист владельца. Нажимаем кнопку страницы, и
ошибиться тут легко: «Слушать» есть и у плеера внизу, и у предыдущей
страницы, которая ещё не сменилась.

Логика чистая, поэтому проверяется без установленного Spotify и без UIA.
"""

from actions.spotify_uia import pick_page_play_button

TRACK = "Люби меня"


def _btn(name: str, area: int):
    """Кнопка как её отдаёт UIA: имя в нижнем регистре, площадь, элемент."""
    return (name.lower(), area, f"<{name}>")


def _page_marker(track: str = TRACK):
    """Кнопка меню в шапке — по ней узнаётся, что открыта страница нужного трека."""
    return _btn(f"открыть контекстное меню: {track}", 900)


def test_picks_big_page_button_not_small_player_button():
    """У плеера внизу тоже есть «Слушать» — брать надо крупную, со страницы."""
    player_play = _btn("слушать", 1_024)
    page_play = _btn("слушать", 9_216)

    action, element = pick_page_play_button([_page_marker(), player_play, page_play], TRACK)

    assert action == "play"
    assert element is page_play[2]


def test_waits_while_previous_page_still_shown():
    """Страница трека ещё не открылась — нажимать нельзя, иначе играет плейлист."""
    stale_page = _btn("открыть контекстное меню: Мой плейлист", 900)

    assert pick_page_play_button([stale_page, _btn("слушать", 9_216)], TRACK) is None


def test_reports_already_playing_when_pause_button_is_shown():
    """Большая «Пауза» вместо «Слушать» — трек уже идёт, повторно жать нечего."""
    result = pick_page_play_button(
        [_page_marker(), _btn("пауза", 9_216), _btn("слушать", 1_024)], TRACK
    )

    assert result == ("playing", None)


def test_english_interface_is_supported():
    """Интерфейс Spotify бывает английским — имена кнопок другие."""
    action, _ = pick_page_play_button(
        [_btn(f"more options for {TRACK}", 900), _btn("play", 9_216)], TRACK
    )

    assert action == "play"


def test_no_track_name_means_no_press():
    """Без названия страницу не опознать — молча ничего не жмём."""
    assert pick_page_play_button([_page_marker(), _btn("слушать", 9_216)], "") is None
