"""Выключить то, чем не пользуешься.

Тридцать пять объявлений уходят в промпт на КАЖДОМ подключении, а сессия
переподключается сама каждые несколько минут. Человеку, у которого нет ни
Spotify, ни Obsidian, ни игр, всё это оплачивается токенами и, что хуже,
сбивает выбор: чем длиннее список, тем чаще модель берёт из него не тот
инструмент.

Проверяется и обратная сторона: выключить можно не всё. Без памяти ассистент
забывает человека, без отмены сделанное нечем вернуть.
"""

import json
import os
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import settings as conv


@pytest.fixture
def конфиг(tmp_path, monkeypatch):
    файл = tmp_path / "api_keys.json"
    monkeypatch.setattr(conv, "_файл", lambda: файл)
    monkeypatch.setattr(conv, "_прочитать",
                        lambda: json.loads(файл.read_text(encoding="utf-8"))
                        if файл.exists() else {})
    return файл


# ─── Список выключенных ──────────────────────────────────────────────────────

def test_по_умолчанию_включено_всё(конфиг):
    assert conv.disabled_tools() == set()
    assert conv.tool_enabled("music_player") is True


def test_выключенное_сохраняется_и_читается(конфиг):
    conv.set_disabled_tools(["music_player", "obsidian"])

    assert conv.disabled_tools() == {"music_player", "obsidian"}
    assert conv.tool_enabled("music_player") is False
    assert conv.tool_enabled("weather") is True


def test_прежний_ключ_плагинов_продолжает_работать(конфиг):
    """У кого-то `plugins_disabled` уже прописан. Молча перестать его
    читать — значит вернуть человеку плагин, который он выключил."""
    конфиг.write_text(json.dumps({"plugins_disabled": ["kettle"]}), encoding="utf-8")

    assert conv.tool_enabled("kettle") is False


def test_оба_списка_складываются(конфиг):
    конфиг.write_text(json.dumps({
        "plugins_disabled": ["kettle"],
        "tools_disabled": ["music_player"],
    }), encoding="utf-8")

    assert conv.disabled_tools() == {"kettle", "music_player"}


def test_прежний_ключ_не_стирается_при_записи(конфиг):
    """Стереть чужую настройку молча — худший способ навести порядок."""
    конфиг.write_text(json.dumps({"plugins_disabled": ["kettle"]}), encoding="utf-8")

    conv.set_disabled_tools(["music_player"])

    данные = json.loads(конфиг.read_text(encoding="utf-8"))
    assert данные["plugins_disabled"] == ["kettle"]
    assert данные["tools_disabled"] == ["music_player"]


def test_ключи_api_переживают_запись(конфиг):
    конфиг.write_text(json.dumps({"gemini_api_key": "секрет"}), encoding="utf-8")

    conv.set_disabled_tools(["obsidian"])

    assert json.loads(конфиг.read_text(encoding="utf-8"))["gemini_api_key"] == "секрет"


def test_мусор_вместо_списка_не_выключает_ничего(конфиг):
    конфиг.write_text(json.dumps({"tools_disabled": "все подряд"}), encoding="utf-8")
    assert conv.disabled_tools() == set()


def test_пробелы_и_повторы_чистятся(конфиг):
    conv.set_disabled_tools([" obsidian ", "obsidian", "", "  "])
    assert conv.disabled_tools() == {"obsidian"}


# ─── Что выключить нельзя ────────────────────────────────────────────────────

@pytest.mark.parametrize("имя", sorted(conv.НЕВЫКЛЮЧАЕМЫЕ))
def test_несущее_нельзя_выключить_даже_правкой_файла(конфиг, имя):
    """Руками в JSON — тоже нельзя: это не защита от человека, а защита от
    состояния, в котором ассистент перестаёт быть собой."""
    конфиг.write_text(json.dumps({"tools_disabled": [имя]}), encoding="utf-8")

    assert conv.tool_enabled(имя) is True
    assert имя not in conv.disabled_tools()


def test_несущее_не_записывается_в_файл(конфиг):
    conv.set_disabled_tools(["undo", "music_player"])

    данные = json.loads(конфиг.read_text(encoding="utf-8"))
    assert данные["tools_disabled"] == ["music_player"]


# ─── Связь с реестром ────────────────────────────────────────────────────────

def test_выключенное_не_попадает_в_реестр(конфиг):
    """Не «загрузить и молчать»: модель не должна видеть способность,
    которой её лишили, иначе она будет её предлагать, а вызов упрётся в
    «неизвестный инструмент»."""
    import main
    from core.action_loader import discover_actions

    conv.set_disabled_tools(["game_launcher", "obsidian"])

    реестр = discover_actions(_BASE / "actions", reserved_names=set(),
                              enabled=main._tool_enabled)

    assert "game_launcher" not in реестр.names()
    assert "obsidian" not in реестр.names()
    assert "weather" in реестр.names()


def test_встроенные_тоже_фильтруются(конфиг):
    """У встроенных нет своего загрузчика, который мог бы их не взять —
    их отсеивает сам main, и забыть про это легко."""
    import main

    conv.set_disabled_tools(["translation"])

    включённые = [t["name"] for t in main.INLINE_TOOLS if main._tool_enabled(t["name"])]

    assert "translation" not in включённые
    assert "recall_memory" in включённые


def test_главный_модуль_фильтрует_оба_списка():
    """Проводка: реестры собираются с `enabled=_tool_enabled`, встроенные —
    через `_INLINE_ENABLED`. Потерять любую из трёх точек легко."""
    исходник = (_BASE / "main.py").read_text(encoding="utf-8")

    assert исходник.count("enabled=_tool_enabled") == 2
    assert "_INLINE_ENABLED = [t for t in INLINE_TOOLS if _tool_enabled" in исходник


def test_вызов_выключенного_объясняется_а_не_отрицается():
    """Сессия могла начаться до того, как инструмент выключили.
    «Неизвестный инструмент» здесь — неправда: он известен, его убрали."""
    исходник = (_BASE / "main.py").read_text(encoding="utf-8")
    assert "выключен в настройках, сэр" in исходник


def test_окно_инструментов_есть():
    from ui import MainWindow
    assert callable(getattr(MainWindow, "_open_tool_switchboard", None))
