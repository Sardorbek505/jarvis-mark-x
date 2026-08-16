"""Настройки языков перевода должны менять то, что потом читается.

До этого main.py правил JSON руками по ключам, которых в схеме нет:
"enabled_languages" (включение языка падало с KeyError на каждый вызов) и
"default_language" (запись уходила в никуда, а пользователю сообщалось
об успехе). Тесты фиксируют оба случая и разбор названия языка.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import translation_manager as tm


DEFAULTS = {
    "target_languages": [
        {"code": "ru", "name": "Russian", "native_name": "Русский", "enabled": True},
        {"code": "fr", "name": "French", "native_name": "Français", "enabled": False},
    ],
    "default_target_language": "ru",
    "source_language": "ru",
    "learning_mode": {"enabled": False, "target_language": "en"},
}


@pytest.fixture
def prefs_file(tmp_path, monkeypatch):
    """Настройки в песочнице — тест не трогает реальный конфиг пользователя."""
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "translation_preferences.json"
    path.write_text(json.dumps(DEFAULTS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tm, "_BASE", tmp_path)
    return path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ── разбор названия языка ────────────────────────────────────────────────────
@pytest.mark.parametrize("value,code", [
    ("english", "en"),        # модели инструмент объявлен именно так
    ("English", "en"),
    ("английский", "en"),     # пользователь говорит по-русски
    ("en", "en"),             # код тоже принимаем
    ("French", "fr"),
    ("Français", "fr"),       # родное имя из настроек
])
def test_язык_узнаётся_по_любому_написанию(prefs_file, value, code):
    assert tm.resolve_language_code(value) == code


def test_незнакомый_язык_не_выдумывается(prefs_file):
    assert tm.resolve_language_code("клингонский") is None
    assert tm.resolve_language_code("") is None


# ── включение/выключение языка (раньше: KeyError на каждый вызов) ────────────
def test_включение_языка_доходит_до_файла(prefs_file):
    assert tm.set_language_enabled("French", True) == "fr"
    langs = {lang["code"]: lang["enabled"] for lang in read(prefs_file)["target_languages"]}
    assert langs["fr"] is True


def test_выключение_языка_доходит_до_файла(prefs_file):
    assert tm.set_language_enabled("русский", False) == "ru"
    langs = {lang["code"]: lang["enabled"] for lang in read(prefs_file)["target_languages"]}
    assert langs["ru"] is False


def test_обещанный_язык_добавляется_если_его_нет(prefs_file):
    """english объявлен модели, но в настройках его не было — не молчим."""
    assert tm.set_language_enabled("english", True) == "en"
    langs = {lang["code"]: lang["enabled"] for lang in read(prefs_file)["target_languages"]}
    assert langs["en"] is True


def test_неизвестный_язык_не_считается_успехом(prefs_file):
    assert tm.set_language_enabled("клингонский", True) is None
    assert len(read(prefs_file)["target_languages"]) == 2   # ничего не дописали


# ── язык по умолчанию (раньше: писался ключ, который никто не читает) ────────
def test_язык_по_умолчанию_виден_читателю(prefs_file):
    assert tm.set_default_language("French") == "fr"
    assert read(prefs_file)["default_target_language"] == "fr"
    assert tm.TranslationPreferences().get_default_target_language() == "fr"


def test_ключ_призрак_больше_не_пишется(prefs_file):
    tm.set_default_language("French")
    assert "default_language" not in read(prefs_file)


def test_неизвестный_язык_не_меняет_умолчание(prefs_file):
    assert tm.set_default_language("клингонский") is None
    assert read(prefs_file)["default_target_language"] == "ru"


# ── режим изучения ───────────────────────────────────────────────────────────
def test_режим_изучения_хранит_код_а_не_название(prefs_file):
    assert tm.set_learning_mode(True, "английский") == "en"
    mode = read(prefs_file)["learning_mode"]
    assert mode["enabled"] is True
    assert mode["target_language"] == "en"


def test_режим_изучения_выключается(prefs_file):
    tm.set_learning_mode(True, "french")
    assert tm.set_learning_mode(False) == ""
    assert read(prefs_file)["learning_mode"]["enabled"] is False


def test_изучение_неизвестного_языка_не_включается(prefs_file):
    assert tm.set_learning_mode(True, "клингонский") is None
    assert read(prefs_file)["learning_mode"]["enabled"] is False
