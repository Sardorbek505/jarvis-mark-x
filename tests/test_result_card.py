"""Карточка результата рядом с шаром."""
import json
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


def _data(tool, args, result):
    return json.loads(build_card(tool, args, {"result": result})["extra"] or "null")


def test_search_results_become_a_list():
    d = _data("web_search", {"query": "q"}, "По запросу «q»: первый | второй | третий")
    assert d == {"card": "list", "heading": "Найдено", "items": ["первый", "второй", "третий"]}


def test_list_keeps_times_but_drops_bullets():
    d = _data("calendar", {"action": "get_events"}, "• 10:00 — Созвон\n2) 13:30 — Обед")
    assert d["items"] == ["10:00 — Созвон", "13:30 — Обед"]


def test_music_card_has_track_and_status():
    d = _data("music_player", {"action": "pause", "query": "Believer"}, "Пауза")
    assert d["card"] == "media" and d["title"] == "Believer" and d["playing"] is False


def test_music_card_says_playing_only_when_it_plays():
    """Живой случай: «люби меня — Играет» с эквалайзером, а Spotify молчит."""
    ok = _data("music_player", {"action": "play", "query": "люби меня"}, "Включил «Люби меня» — Artik.")
    assert ok["playing"] is True and ok["status"] == "Играет"
    for said in ("Отправил «Люби меня» в Spotify, но не вижу, что заиграло — проверьте Spotify на компьютере.",
                 "Открыл «люби меня» в Spotify, но воспроизведение не началось — нажмите Play.",
                 "Spotify не запустился на компьютере."):
        d = _data("music_player", {"action": "play", "query": "люби меня"}, said)
        assert d["playing"] is False and d["status"] != "Играет", said


def test_volume_card_reads_level():
    d = _data("computer_control", {"action": "volume_up", "value": "70"}, "ok")
    assert d == {"card": "meter", "meter": "volume", "level": 70, "label": "Громкость"}
    c = build_card("computer_control", {"action": "volume_up", "value": "70"}, "ok")
    assert c["address"] == "громкость"


def test_timer_card_counts_down_from_now():
    import time
    d = _data("sleep_timer", {"action": "set", "duration_minutes": 30}, "ok")
    assert d["card"] == "timer" and d["total"] == 1800
    assert abs(d["due"] - (time.time() + 1800)) < 5


def test_translation_message_memory_app():
    assert _data("translation", {"text": "привет", "target_language": "english"}, "hello")["dst"] == "hello"
    assert _data("send_to_telegram", {"text": "буду"}, "Сэр, сообщение успешно отправлено")["text"] == "буду"
    assert _data("save_to_memory", {"key": "др", "value": "12 марта"}, "ok")["value"] == "12 марта"
    assert _data("browser", {"url": "https://www.youtube.com/x"}, "ok")["name"] == "youtube.com"


def test_failure_becomes_error_card():
    assert _data("files", {"path": "C:/Windows"}, "Ошибка: защищено")["card"] == "error"
    assert _data("send_to_telegram", {}, "Сэр, произошла ошибка при отправке")["card"] == "error"
