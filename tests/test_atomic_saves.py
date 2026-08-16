"""Сохранение состояния не должно оставлять обрезанный файл.

Было: восемь модулей писали JSON через `open(path, 'w')` + `json.dump`, минуя
`core/storage.atomic_write_json`, который в проекте для этого и заведён.
`open(..., 'w')` сначала обнуляет файл — обрыв на середине (питание, kill,
несериализуемые данные) оставлял битый JSON вместо календаря, напоминаний
или refresh-токена Spotify. Ошибку при этом глотали: `return False` без
единой строчки в лог.

Здесь проверяется главное свойство: после неудачной записи прежний файл цел.
"""

import json
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import calendar_manager, news_manager, smart_reminders, translation_manager  # noqa: E402


class _Unserializable:
    """json.dump на этом падает — имитация сбоя посреди записи."""


def _existing(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_календарь_не_бьётся_при_сбое_записи(tmp_path, monkeypatch):
    target = tmp_path / "calendar.json"
    _existing(target, {"events": ["встреча в три"]})
    monkeypatch.setattr(calendar_manager, "CALENDAR_FILE", target)

    ok = calendar_manager._save_calendar({"events": [_Unserializable()]})

    assert ok is False, "провал записи должен быть виден вызывающему"
    assert json.loads(target.read_text(encoding="utf-8")) == {"events": ["встреча в три"]}
    assert not list(tmp_path.glob("*.tmp")), "временный файл не должен оставаться"


def test_напоминания_не_бьются_при_сбое_записи(tmp_path, monkeypatch):
    target = tmp_path / "patterns.json"
    _existing(target, {"utro": 7})
    monkeypatch.setattr(smart_reminders, "_PATTERNS_FILE", target)

    assert smart_reminders._save_patterns({"bad": _Unserializable()}) is False
    assert json.loads(target.read_text(encoding="utf-8")) == {"utro": 7}


def test_провал_записи_попадает_в_лог(tmp_path, monkeypatch, caplog):
    """Молчаливый `return False` — то, из-за чего пропажу нельзя объяснить."""
    target = tmp_path / "calendar.json"
    monkeypatch.setattr(calendar_manager, "CALENDAR_FILE", target)

    with caplog.at_level("ERROR"):
        calendar_manager._save_calendar({"events": [_Unserializable()]})

    assert any("calendar.json" in r.getMessage() for r in caplog.records), \
        "в логе должно быть имя файла"


def test_удачная_запись_читается_обратно(tmp_path, monkeypatch):
    target = tmp_path / "calendar.json"
    monkeypatch.setattr(calendar_manager, "CALENDAR_FILE", target)

    assert calendar_manager._save_calendar({"events": ["звонок"]}) is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"events": ["звонок"]}


@pytest.mark.parametrize("module_name", ["translation_manager", "news_manager"])
def test_модуль_использует_атомарную_запись(module_name):
    """Страховка от возврата к `open(..., 'w')` в этих модулях."""
    src = (_BASE / "core" / f"{module_name}.py").read_text(encoding="utf-8")
    assert "atomic_write_json" in src
    assert "'w', encoding='utf-8'" not in src, "прямая перезапись вернулась"


def test_остальные_сохранения_тоже_атомарны():
    """Эти три пути не были покрыты ничем: забытый импорт `atomic_write_json`
    поймал только линтер, а тесты остались зелёными. Значит нужен тест."""
    from core import proactive_engine, team_collaboration
    from actions import modes
    from core.storage import atomic_write_json as canonical

    assert modes.atomic_write_json is canonical
    assert proactive_engine.atomic_write_json is canonical
    assert team_collaboration.atomic_write_json is canonical


def test_режим_сохраняется_и_читается(tmp_path, monkeypatch):
    from actions import modes

    target = tmp_path / "mode_state.json"
    monkeypatch.setattr(modes, "_STATE_PATH", target)

    modes._save_state("work", "code")

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "mode": "work", "preference": "code",
    }


def test_модули_видят_общее_хранилище():
    """Импорт core.storage не должен разъезжаться между модулями."""
    from core.storage import atomic_write_json as canonical
    assert translation_manager.atomic_write_json is canonical
    assert news_manager.atomic_write_json is canonical
    assert smart_reminders.atomic_write_json is canonical
    assert calendar_manager.atomic_write_json is canonical
