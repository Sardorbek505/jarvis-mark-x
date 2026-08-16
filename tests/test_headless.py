"""Запуск без окна.

Главная проверка здесь — последняя: `HeadlessUI` обязана покрывать всё, что
`Jarvis` дёргает у интерфейса. Стоит кому-то дописать в main.py новое
`self.ui.что_то()`, и headless свалится с AttributeError посреди разговора,
а не при старте. Поэтому список сверяется автоматически, разбором main.py.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core.headless_ui import HeadlessUI, headless_requested  # noqa: E402


# ─── Включение ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("значение,ожидание", [
    ("1", True), ("true", True), ("да", True),
    ("", False), ("0", False), ("false", False),
])
def test_переменная_окружения_включает_режим(monkeypatch, значение, ожидание):
    monkeypatch.setenv("JARVIS_HEADLESS", значение)
    assert headless_requested(argv=["main.py"]) is ожидание


def test_флаг_командной_строки_включает_режим(monkeypatch):
    monkeypatch.delenv("JARVIS_HEADLESS", raising=False)
    assert headless_requested(argv=["main.py", "--headless"]) is True
    assert headless_requested(argv=["main.py"]) is False


# ─── Ключ ─────────────────────────────────────────────────────────────────────

def test_без_ключа_падаем_внятно_а_не_виснем(tmp_path, monkeypatch):
    """Ради этого headless и затевался: окно бы молча ждало человека."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    ui = HeadlessUI(config_path=tmp_path / "api_keys.json")

    with pytest.raises(RuntimeError) as ошибка:
        ui.wait_for_api_key()

    текст = str(ошибка.value)
    assert "GEMINI_API_KEY" in текст, "в ошибке должно быть, что именно задать"
    assert "api_keys.json" in текст, "в ошибке должен быть путь к конфигу"


def test_ключ_из_переменной_окружения_подходит(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-тестовый-ключ")
    ui = HeadlessUI(config_path=tmp_path / "api_keys.json")

    assert ui.wait_for_api_key() == "AIza-тестовый-ключ"


# ─── Поведение ────────────────────────────────────────────────────────────────

def test_лог_уходит_в_консоль_и_копится(caplog):
    ui = HeadlessUI()
    with caplog.at_level("INFO", logger="JARVIS"):
        ui.write_log("SYS: проверка")

    assert "SYS: проверка" in ui.logs
    assert any("проверка" in r.getMessage() for r in caplog.records)


def test_состояние_запоминается():
    ui = HeadlessUI()
    ui.set_state("LISTENING")
    assert ui.state == "LISTENING"


def test_без_терминала_поток_ввода_не_поднимается(monkeypatch):
    """Под перенаправленным вводом тред сразу упёрся бы в EOF."""
    monkeypatch.setattr(sys, "stdin", None)
    ui = HeadlessUI()
    ui.start_text_input()
    assert ui._stdin_thread is None


# ─── Сверка с тем, что реально нужно Jarvis ───────────────────────────────────

def _что_jarvis_просит_у_окна() -> set[str]:
    """Собирает все обращения `self.ui.X` в main.py."""
    src = (_BASE / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    имена = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "ui"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"):
            имена.add(node.attr)
    return имена


def test_headless_покрывает_весь_интерфейс_окна():
    нужно = _что_jarvis_просит_у_окна()
    assert нужно, "разбор main.py ничего не нашёл — тест сломан, а не код"

    ui = HeadlessUI()
    нет = sorted(имя for имя in нужно if not hasattr(ui, имя))

    assert not нет, (
        "HeadlessUI не покрывает обращения из main.py: " + ", ".join(нет)
        + "\nдобавьте их в core/headless_ui.py, иначе запуск без окна упадёт"
    )


def test_main_действительно_разветвляется_на_headless():
    """Страховка от того, что режим отвяжут от точки входа."""
    src = (_BASE / "main.py").read_text(encoding="utf-8")
    точка_входа = src[src.index("def main():"):]

    assert "headless_requested()" in точка_входа
    assert "HeadlessUI()" in точка_входа
    assert re.search(r"if headless:", точка_входа), "ветка headless потерялась"
