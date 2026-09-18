"""Напоминание должно сработать, даже если ДЖАРВИС закрыт.

`actions/calendar.py` держит напоминания в процессе: закрыли приложение —
напоминания нет, и человек узнаёт об этом ровно тогда, когда оно не сработало.
Здесь напоминание уходит в планировщик ОС.

Сами планировщики тут не запускаются: проверяются команды, которые им уходят,
разбор времени, учёт поставленного и — главное — что отказ планировщика не
выдаётся за успех.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import os_reminder as r


@pytest.fixture(autouse=True)
def своя_папка(tmp_path, monkeypatch):
    """Ни журнал, ни plist не должны трогать домашнюю папку разработчика."""
    monkeypatch.setattr(r, "_dir", lambda: tmp_path)
    monkeypatch.setattr(r, "_журнал", lambda: tmp_path / "scheduled.json")


@pytest.fixture
def планировщик(monkeypatch):
    """Подменяет вызов планировщика и запоминает, с чем его звали."""
    вызовы = []

    def записать(cmd, *a, **kw):
        вызовы.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(r.subprocess, "run", записать)
    return вызовы


_ЧЕРЕЗ_ЧАС = "через 1 час"


# ─── Команды планировщиков ────────────────────────────────────────────────────

def test_windows_ставит_разовую_задачу(monkeypatch):
    monkeypatch.setattr(r, "_OS", "Windows")
    команда = r._schtasks_command("JARVIS_x", datetime(2026, 3, 3, 8, 30), "позвонить")

    assert команда[:3] == ["schtasks", "/create", "/tn"]
    assert "/sc" in команда and команда[команда.index("/sc") + 1] == "once"
    assert "03/03/2026" in команда and "08:30" in команда
    assert "powershell" in команда[команда.index("/tr") + 1]


def test_macos_пишет_plist_с_календарным_интервалом(monkeypatch):
    monkeypatch.setattr(r, "_OS", "Darwin")
    plist = r._launchd_plist("JARVIS_x", datetime(2026, 3, 3, 8, 30), "позвонить")

    assert "<key>Label</key><string>JARVIS_x</string>" in plist
    assert "<key>Hour</key><integer>8</integer>" in plist
    assert "<key>Minute</key><integer>30</integer>" in plist
    assert "osascript" in plist


def test_linux_ставит_разовый_таймер(monkeypatch):
    monkeypatch.setattr(r, "_OS", "Linux")
    команда = r._systemd_command("JARVIS_x", datetime(2026, 3, 3, 8, 30), "позвонить")

    assert команда[:2] == ["systemd-run", "--user"]
    assert "--on-calendar=2026-03-03 08:30:00" in команда
    assert "--unit=JARVIS_x" in команда
    assert "notify-send" in команда


# ─── Текст из чужих рук ───────────────────────────────────────────────────────

@pytest.mark.parametrize("опасное", [
    "позвонить; rm -rf /",
    'напомнить "про встречу"',
    "текст\nс переводом строки",
    "команда && другая",
    "обратный `апостроф`",
])
def test_сообщение_чистится_перед_командной_строкой(опасное):
    """Сообщение сочиняет модель со слов человека и оно уходит в команду ОС."""
    чистое = r._safe(опасное)

    for символ in ('"', "'", "`", ";", "&", "|", "<", ">", "\\", "\n"):
        assert символ not in чистое


def test_длинное_сообщение_обрезается():
    assert len(r._safe("я" * 500)) <= 200


# ─── Постановка ───────────────────────────────────────────────────────────────

def test_напоминание_ставится_и_попадает_в_журнал(планировщик):
    ответ = r.os_reminder({"message": "позвонить маме", "when": _ЧЕРЕЗ_ЧАС})

    assert "Напомню" in ответ and "позвонить маме" in ответ
    assert len(планировщик) == 1
    assert len(r._прочитать()) == 1


def test_отказ_планировщика_не_выдаётся_за_успех(monkeypatch):
    """Худший исход — человек уходит уверенным, что его разбудят."""
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="Отказано в доступе"))

    ответ = r.os_reminder({"message": "важное", "when": _ЧЕРЕЗ_ЧАС})

    assert "НЕ поставлено" in ответ
    assert "Отказано в доступе" in ответ
    assert r._прочитать() == [], "несостоявшееся напоминание в журнале не нужно"


def test_отсутствие_планировщика_названо_прямо(monkeypatch):
    def нет_такого(*a, **kw):
        raise FileNotFoundError("schtasks")

    monkeypatch.setattr(r.subprocess, "run", нет_такого)
    ответ = r.os_reminder({"message": "важное", "when": _ЧЕРЕЗ_ЧАС})

    assert "НЕ поставлено" in ответ
    assert "недоступен" in ответ


def test_зависший_планировщик_не_вешает_ассистента(monkeypatch):
    def висит(*a, **kw):
        raise r.subprocess.TimeoutExpired(cmd="schtasks", timeout=15)

    monkeypatch.setattr(r.subprocess, "run", висит)
    assert "НЕ поставлено" in r.os_reminder({"message": "x", "when": _ЧЕРЕЗ_ЧАС})


def test_непонятное_время_просит_сказать_иначе(планировщик):
    ответ = r.os_reminder({"message": "позвонить", "when": "когда-нибудь потом"})

    assert "Не разобрал время" in ответ
    assert планировщик == [], "в планировщик при этом не лезем"


def test_прошедшее_время_отклоняется(планировщик):
    ответ = r.os_reminder({"message": "поздно", "when": "вчера в 10:00"})

    assert "прошло" in ответ or "Не разобрал" in ответ
    assert планировщик == []


def test_пустое_сообщение_переспрашивают(планировщик):
    assert "О чём напомнить" in r.os_reminder({"when": _ЧЕРЕЗ_ЧАС})
    assert планировщик == []


# ─── Список и отмена ──────────────────────────────────────────────────────────

def test_список_пуст_когда_нечего_показывать():
    assert "нет" in r.os_reminder({"action": "list"})


def test_список_показывает_поставленное(планировщик):
    r.os_reminder({"message": "позвонить маме", "when": _ЧЕРЕЗ_ЧАС})
    r.os_reminder({"message": "забрать посылку", "when": "через 2 часа"})

    ответ = r.os_reminder({"action": "list"})

    assert "позвонить маме" in ответ and "забрать посылку" in ответ


def test_сработавшее_напоминание_уходит_из_списка(планировщик):
    r._записать([
        {"name": "JARVIS_old", "when": (datetime.now() - timedelta(hours=1)).isoformat(),
         "text": "уже прошло"},
        {"name": "JARVIS_new", "when": (datetime.now() + timedelta(hours=1)).isoformat(),
         "text": "ещё впереди"},
    ])

    ответ = r.os_reminder({"action": "list"})

    assert "ещё впереди" in ответ
    assert "уже прошло" not in ответ, "прошедшее напоминание — не напоминание"


def test_отмена_по_слову_из_текста(планировщик):
    r.os_reminder({"message": "позвонить маме", "when": _ЧЕРЕЗ_ЧАС})

    ответ = r.os_reminder({"action": "cancel", "message": "маме"})

    assert "Отменил" in ответ
    assert r._прочитать() == []


def test_отмена_несуществующего_не_врёт(планировщик):
    r.os_reminder({"message": "позвонить маме", "when": _ЧЕРЕЗ_ЧАС})
    ответ = r.os_reminder({"action": "cancel", "message": "полёт на луну"})

    assert "не нашёл" in ответ
    assert len(r._прочитать()) == 1, "чужое напоминание снимать не за что"


def test_битый_журнал_не_роняет_инструмент(tmp_path):
    (tmp_path / "scheduled.json").write_text("{не json", encoding="utf-8")
    assert "нет" in r.os_reminder({"action": "list"})


# ─── Как это звучит ───────────────────────────────────────────────────────────

def test_время_называется_словами():
    сейчас = datetime.now()
    assert "сегодня в" in r._когда_словами(сейчас.replace(hour=23, minute=59))
    assert "завтра в" in r._когда_словами(сейчас + timedelta(days=1))
    assert "послезавтра в" in r._когда_словами(сейчас + timedelta(days=2))


def test_дальняя_дата_называется_месяцем():
    assert r._когда_словами(datetime(2026, 3, 3, 8, 30)).startswith("3 марта в")


def test_инструмент_подхвачен_реестром():
    import main

    assert main._ACTIONS.has("os_reminder")


def test_описание_отделяет_его_от_календаря():
    """Два похожих инструмента модель путает, если ей не сказать разницу."""
    описание = r.TOOL["description"].lower()

    assert "закрыт" in описание, "в этом вся разница с календарём"
    assert "calendar" in описание


# ─── Разбор времени: молчаливая подстановка девяти утра ───────────────────────

def test_непонятное_время_не_превращается_в_девять_утра():
    """`_parse_datetime` объявлена как Optional и все три вызывающих места
    проверяют результат на None — но получить None было невозможно: любая
    непонятная фраза доезжала до «сегодня в 9:00». То есть «напомни
    когда-нибудь» молча ставило напоминание на девять утра, а «добавь встречу»
    без времени — встречу на сегодняшнее утро. Проверки были, защиты не было."""
    from core.calendar_manager import _parse_datetime

    for бессмыслица in ("когда-нибудь потом", "абракадабра", "", "позвонить маме"):
        assert _parse_datetime(бессмыслица) is None, бессмыслица


@pytest.mark.parametrize("фраза", [
    "через 30 минут", "завтра в 14:00", "сегодня", "завтра утром",
    "в 23:50", "послезавтра вечером",
])
def test_понятное_время_по_прежнему_разбирается(фраза):
    from core.calendar_manager import _parse_datetime

    assert _parse_datetime(фраза) is not None, фраза


def test_вчера_остаётся_в_прошлом():
    """Раньше «вчера в 10:00» превращалось в сегодняшние десять утра —
    напоминание на время, которого человек не называл."""
    from datetime import date

    from core.calendar_manager import _parse_datetime

    момент = _parse_datetime("вчера в 10:00")

    assert момент is not None
    assert момент.date() < date.today()


def test_календарь_отказывается_от_непонятного_времени(tmp_path, monkeypatch):
    """Обратная сторона: проверка в calendar_manager наконец достижима."""
    from core import calendar_manager as cm

    monkeypatch.setattr(cm, "CALENDAR_FILE", tmp_path / "calendar.json")

    ответ = cm.add_event("встреча", "когда-нибудь")

    assert "Не понял" in ответ
