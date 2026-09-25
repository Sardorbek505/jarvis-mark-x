"""Поиск программ и окон по-русски, с ошибками и по процессу."""
from core import win_apps as wa
from core.win_apps import App, Window

APPS = [
    App("Telegram Desktop", appid="TelegramDesktop"),
    App("Google Chrome", appid="Chrome"),
    App("Uninstall Google Chrome", path="x.lnk"),       # мусор отсеивается индексом
    App("Spotify", appid="SpotifyAB.Spotify"),
    App("Steam", path="C:/Steam/steam.lnk"),
    App("Visual Studio Code", path="code.lnk"),
    App("Microsoft Word", appid="Word"),
    App("Discord", path="discord.lnk"),
]


def test_canonical_russian_names():
    assert wa.canonical("Телега") == "telegram"
    assert wa.canonical("  Хром ") == "chrome"
    assert wa.canonical("программу стим") == "steam"
    assert wa.canonical("вс код") == "visual studio code"


def test_find_app_by_russian_and_english():
    assert wa.find_app("телеграм", APPS).name == "Telegram Desktop"
    assert wa.find_app("хром", APPS).name == "Google Chrome"
    assert wa.find_app("стим", APPS).name == "Steam"
    assert wa.find_app("ворд", APPS).name == "Microsoft Word"
    assert wa.find_app("вс код", APPS).name == "Visual Studio Code"


def test_find_app_with_typo():
    assert wa.find_app("spotfy", APPS).name == "Spotify"
    assert wa.find_app("discrd", APPS).name == "Discord"


def test_unknown_app_is_none():
    assert wa.find_app("фотошоп", APPS) is None


WINS = [
    Window(1, "YouTube — Google Chrome", 10, "chrome.exe"),
    Window(2, "Imagine Dragons - Believer", 11, "spotify.exe"),   # у Spotify в заголовке песня
    Window(3, "Telegram", 12, "telegram.exe"),
    Window(4, "Документ1 - Word", 13, "winword.exe"),
]


def test_find_windows_by_process_not_title():
    assert [w.hwnd for w in wa.find_windows("хром", WINS)] == [1]
    assert [w.hwnd for w in wa.find_windows("спотифай", WINS)] == [2]
    assert [w.hwnd for w in wa.find_windows("ворд", WINS)] == [4]


def test_find_windows_by_title_fallback():
    assert [w.hwnd for w in wa.find_windows("документ1", WINS)] == [4]
    assert wa.find_windows("zoom", WINS) == []
