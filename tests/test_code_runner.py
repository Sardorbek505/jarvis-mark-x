"""Запуск скрипта — за кнопкой, и разбор кода — без запуска.

Микрофон отдаёт модели всё, что слышит в комнате, включая звук из фильма.
«Запусти скрипт» — единственное действие во всём наборе, выполняющее
ПРОИЗВОЛЬНЫЙ код: отменить его нельзя, и что именно он сделает, заранее не
знает никто.

Поэтому главное здесь — не то, что скрипт запускается, а то, что он не
запускается мимо гейта: в частности, когда модель просто не передала
параметр `action`.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import code_runner as cr


class _Окно:
    def __init__(self, брошенный=""):
        self.current_file = брошенный
        self.лог: list[str] = []

    def write_log(self, текст):
        self.лог.append(текст)


# ─── Гейт ────────────────────────────────────────────────────────────────────

def test_запуск_проходит_через_подтверждение():
    """Иначе произвольный код выполняется по фразе из телевизора."""
    import main

    assert main._is_destructive("code_runner", {"action": "run"}) is True


def test_пропущенное_действие_тоже_считается_запуском():
    """Спрашивай мы про «run», как про «delete», модель обошла бы гейт,
    просто не передав параметр: по умолчанию здесь именно запуск."""
    import main

    assert main._is_destructive("code_runner", {}) is True
    assert main._is_destructive("code_runner", {"file_path": "x.py"}) is True


def test_разбор_без_запуска_подтверждения_не_требует():
    """`check` ничего не выполняет — спрашивать не о чем."""
    import main

    assert main._is_destructive("code_runner", {"action": "check"}) is False


def test_у_запуска_есть_понятный_заголовок_баннера():
    """«code_runner / » на кнопке — это не то, по чему принимают решение."""
    import main

    assert main._DESTRUCTIVE_TITLES["code_runner/run"] == "Запуск программы"
    assert main._DESTRUCTIVE_TITLES["code_runner/"] == "Запуск программы"


# ─── Разбор без запуска ──────────────────────────────────────────────────────

def test_синтаксическая_ошибка_называется_со_строкой(tmp_path):
    файл = tmp_path / "кривой.py"
    файл.write_text("def f(:\n    pass\n", encoding="utf-8")

    ответ = cr.code_runner({"action": "check", "file_path": str(файл)})

    assert "кривой.py" in ответ
    assert "строка 1" in ответ


def test_проверка_ничего_не_выполняет(tmp_path):
    """Самое важное свойство `check`: ни одна строка скрипта не работает."""
    след = tmp_path / "я-был-запущен"
    файл = tmp_path / "опасный.py"
    файл.write_text(f"open({str(след)!r}, 'w').write('да')\n", encoding="utf-8")

    cr.code_runner({"action": "check", "file_path": str(файл)})

    assert not след.exists()


def test_целый_файл_проходит_проверку(tmp_path):
    файл = tmp_path / "хороший.py"
    файл.write_text("print('всё в порядке')\n", encoding="utf-8")

    assert "в порядке" in cr.code_runner({"action": "check", "file_path": str(файл)})


def test_не_python_разобрать_нельзя_и_об_этом_говорится(tmp_path):
    файл = tmp_path / "скрипт.sh"
    файл.write_text("echo привет\n", encoding="utf-8")

    ответ = cr.code_runner({"action": "check", "file_path": str(файл)})

    assert "Python" in ответ


# ─── Запуск ──────────────────────────────────────────────────────────────────

def test_вывод_скрипта_попадает_в_ответ(tmp_path):
    файл = tmp_path / "привет.py"
    файл.write_text("print('сорок два')\n", encoding="utf-8")

    ответ = cr.code_runner({"action": "run", "file_path": str(файл)})

    assert "сорок два" in ответ


def test_молчаливый_скрипт_не_выдаётся_за_пустой_ответ(tmp_path):
    файл = tmp_path / "тихий.py"
    файл.write_text("x = 1\n", encoding="utf-8")

    ответ = cr.code_runner({"action": "run", "file_path": str(файл)})

    assert "без ошибок" in ответ


def test_упавший_скрипт_называет_ошибку_а_не_отчитывается_об_успехе(tmp_path):
    файл = tmp_path / "падучий.py"
    файл.write_text("raise ValueError('так нельзя')\n", encoding="utf-8")

    ответ = cr.code_runner({"action": "run", "file_path": str(файл)})

    assert "ошибкой" in ответ
    assert "так нельзя" in ответ


def test_аргументы_доходят_до_скрипта(tmp_path):
    файл = tmp_path / "эхо.py"
    файл.write_text("import sys; print('вижу', *sys.argv[1:])\n", encoding="utf-8")

    ответ = cr.code_runner(
        {"action": "run", "file_path": str(файл), "args": "один два"})

    assert "вижу один два" in ответ


def test_зависший_скрипт_останавливается(tmp_path):
    """Иначе один `while True` вешает голосовой ход навсегда."""
    файл = tmp_path / "вечный.py"
    файл.write_text("import time; time.sleep(30)\n", encoding="utf-8")

    ответ = cr.code_runner(
        {"action": "run", "file_path": str(файл), "timeout": 1})

    assert "не уложился" in ответ


def test_скрипт_работает_в_своей_папке(tmp_path):
    """Скрипт почти всегда рассчитывает на соседние файлы, а не на то,
    откуда его позвали."""
    (tmp_path / "сосед.txt").write_text("рядом", encoding="utf-8")
    файл = tmp_path / "читатель.py"
    файл.write_text("print(open('сосед.txt', encoding='utf-8').read())\n",
                    encoding="utf-8")

    assert "рядом" in cr.code_runner({"action": "run", "file_path": str(файл)})


def test_многословный_вывод_обрезается(tmp_path):
    """Скрипт может напечатать мегабайт. Зачитывать его вслух — не помощь."""
    файл = tmp_path / "болтун.py"
    файл.write_text("print('а' * 5000)\n", encoding="utf-8")

    ответ = cr.code_runner({"action": "run", "file_path": str(файл)})

    assert len(ответ) < 700
    assert ответ.endswith("…")


def test_неизвестное_расширение_не_запускается_наугад(tmp_path):
    файл = tmp_path / "нечто.xyz"
    файл.write_text("что-то\n", encoding="utf-8")

    ответ = cr.code_runner({"action": "run", "file_path": str(файл)})

    assert "не знаю, чем запускать" in ответ.lower()


def test_отсутствие_интерпретатора_объясняется(tmp_path, monkeypatch):
    """«Ничего не произошло» — худший из возможных ответов."""
    файл = tmp_path / "скрипт.js"
    файл.write_text("console.log(1)\n", encoding="utf-8")
    monkeypatch.setitem(cr._ЧЕМ, ".js", lambda: [])

    ответ = cr.code_runner({"action": "run", "file_path": str(файл)})

    assert "нечем" in ответ


# ─── Путь ────────────────────────────────────────────────────────────────────

def test_брошенный_в_окно_файл_берётся_без_пути(tmp_path):
    файл = tmp_path / "брошенный.py"
    файл.write_text("print('взял')\n", encoding="utf-8")

    ответ = cr.code_runner({"action": "run"}, player=_Окно(брошенный=str(файл)))

    assert "взял" in ответ


def test_без_файла_инструмент_переспрашивает():
    assert "?" in cr.code_runner({"action": "run"}, player=_Окно())


def test_несуществующий_файл_говорится_прямо(tmp_path):
    ответ = cr.code_runner({"action": "run", "file_path": str(tmp_path / "нет.py")})
    assert "нет по пути" in ответ
