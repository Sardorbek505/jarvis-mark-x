"""Самопроверка после команды: снимок экрана → «вышло ли». Не вышло — Джарвис
говорит и пробует иначе (но не по кругу); вышло — только галочка в журнале."""
import threading

import pytest

from core import verify as vf


def _verifier(verdicts):
    asked, said, logged = [], [], []
    done = threading.Event()
    answers = iter(verdicts)

    def ask(jpeg, task, reported):
        asked.append((jpeg, task, reported))
        return next(answers)

    v = vf.Verifier(capture=lambda: b"\xff\xd8jpeg", ask=ask, delay=0)
    v.say = said.append
    v.log = lambda text: (logged.append(text), done.set())
    return v, asked, said, logged, done


def _run(v, done, *call):
    done.clear()
    assert v.after(*call)
    assert done.wait(2)


@pytest.mark.parametrize("name,args,result,want", [
    ("open_app", {"app_name": "Telegram"}, "Открыл Telegram.", True),
    ("open_app", {"app_name": "Telegram"}, "Не удалось найти Telegram.", False),   # и так ясно
    ("window_control", {"action": "close", "target": "хром"}, "Закрыл.", True),
    ("window_control", {"action": "run"}, "Открыл.", False),
    ("video_control", {"action": "fullscreen"}, "На весь экран.", True),
    ("video_control", {"action": "pause"}, "Пауза.", False),                     # проверено плеером
    ("music_player", {"action": "play"}, "Включил.", False),                       # проверено Spotify
    ("computer_control", {"action": "volume_set"}, "Громкость 50.", False),
])
def test_what_is_checked(name, args, result, want):
    assert vf.should_verify(name, args, result) is want


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("JARVIS_VERIFY", "0")
    assert not vf.should_verify("open_app", {"app_name": "x"}, "Открыл.")


def test_parse_verdict():
    assert vf.parse_verdict('{"ok": true, "note": ""}') == (True, "")
    assert vf.parse_verdict('```json\n{"ok": false, "note": "окно ошибки"}\n```') == (False, "окно ошибки")
    assert vf.parse_verdict("Да, открыто") == (True, "")
    assert vf.parse_verdict("нет") == (False, "")
    assert vf.parse_verdict("хм") == (None, "")
    assert vf.parse_verdict('{"ok": "maybe"}') == (None, "")


def test_success_is_silent():
    v, asked, said, logged, done = _verifier([(True, "")])
    _run(v, done, "open_app", {"app_name": "Telegram"}, "Открыл Telegram.")
    assert not said and logged == ["SYS: ✓ проверил: открыть программу «Telegram» (её окно должно быть на "
                                   "экране, впереди)"]
    assert asked[0][2] == "Открыл Telegram."


def test_failure_speaks_then_does_not_loop():
    v, asked, said, logged, done = _verifier([(False, "видно окно ошибки"), (False, "")])
    _run(v, done, "open_app", {"app_name": "Telegram"}, "Открыл Telegram.")
    assert "НЕ получилось: видно окно ошибки" in said[0] and "другой способ" in said[0]
    assert "окно ошибки" in logged[0]
    _run(v, done, "open_app", {"app_name": "Telegram"}, "Открыл Telegram.")   # вторая попытка — тоже нет
    assert "Не пробуй снова" in said[1]


def test_unknown_verdict_or_no_screenshot_stays_quiet():
    v, asked, said, logged, done = _verifier([(None, "")])
    finished = threading.Event()
    real_check = v.check
    v.check = lambda *a: (lambda r: (finished.set(), r)[1])(real_check(*a))
    assert v.after("browser", {"url": "vk.com"}, "Открыл vk.com.")
    assert finished.wait(2)
    v.capture = lambda: None
    assert v.check("browser", {"url": "vk.com"}, "") == (None, "")
    assert not said and not logged
