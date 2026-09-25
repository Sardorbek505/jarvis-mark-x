"""Карточка результата рядом с шаром."""
import sys

from core.result_card import build_card, capture_foreground_png


def test_search_card_shows_query_and_result():
    c = build_card("web_search", {"query": "погода в Токио"}, {"result": "Токио: +18°C"})
    assert c["title"] == "Поиск"
    assert c["address"] == "поиск: погода в Токио"
    assert c["body"] == "Токио: +18°C"


def test_browser_card_uses_url_and_wants_window_shot_on_windows():
    c = build_card("browser", {"action": "go_to", "url": "https://youtube.com"}, "Открыл")
    assert c["address"] == "https://youtube.com"
    assert c["want_shot"] is (sys.platform == "win32")


def test_long_result_is_cut_on_a_word():
    c = build_card("web_search", {"query": "x"}, {"result": "слово " * 200})
    assert len(c["body"]) <= 421 and c["body"].endswith("…")
    assert not c["body"][:-1].endswith(" ")


def test_service_tools_get_no_card():
    assert build_card("set_mode", {}, "ok") is None
    assert build_card("shutdown_jarvis", {}, "ok") is None


def test_unknown_tool_still_gets_a_readable_card():
    c = build_card("new_tool", {}, None)
    assert c["title"] == "New tool" and c["address"] == "jarvis://new_tool"


def test_no_window_shot_outside_windows():
    if sys.platform != "win32":
        assert capture_foreground_png() is None
