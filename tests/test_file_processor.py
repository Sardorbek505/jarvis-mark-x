"""Бросил файл в окно — и спросил вслух.

Файл на экране и ассистент рядом, а между ними не было ничего: чтобы спросить
«о чём этот договор», надо было открыть PDF, выделить, скопировать и
продиктовать. Проще прочитать самому — и ассистент оставался не при делах ровно
там, где мог бы сэкономить полчаса.

Модель здесь не вызывается: проверяется определение типа, чтение форматов и то,
что отсутствующий пакет называет себя, а не превращается в «не получилось».
"""

import json
import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import file_processor as fp


class _Окно:
    def __init__(self, current_file=None):
        self.current_file = current_file
        self.лог = []

    def write_log(self, text):
        self.лог.append(text)


@pytest.fixture(autouse=True)
def без_модели(monkeypatch):
    """Пересказ подменяется: проверяем чтение, а не Gemini."""
    monkeypatch.setattr(fp, "_пересказать", lambda т, в, и: "")


# ─── Определение типа ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("имя,вид", [
    ("фото.jpg", "image"), ("скрин.PNG", "image"),
    ("договор.pdf", "pdf"), ("письмо.docx", "docx"),
    ("отчёт.xlsx", "excel"), ("данные.json", "json"),
    ("скрипт.py", "code"), ("заметка.txt", "text"),
    ("архив.zip", "unknown"),
])
def test_тип_по_расширению(имя, вид):
    assert fp._тип(Path(имя)) == вид


# ─── Чтение форматов ──────────────────────────────────────────────────────────

def test_текстовый_файл_читается(tmp_path):
    файл = tmp_path / "заметка.txt"
    файл.write_text("Здесь написано важное", encoding="utf-8")

    ответ = fp.file_processor({"file_path": str(файл)})

    assert "важное" in ответ


def test_длинный_файл_обрезается(tmp_path):
    """Договор на сто страниц — полмиллиона знаков; всё сразу и не нужно."""
    файл = tmp_path / "договор.txt"
    файл.write_text("я" * (fp._MAX_TEXT + 5000), encoding="utf-8")

    текст, _ = fp._читать_текст(файл)

    assert len(текст) <= fp._MAX_TEXT


def test_json_разбирается_и_описывается(tmp_path):
    файл = tmp_path / "данные.json"
    файл.write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")

    текст, беда = fp._читать_json(файл)

    assert not беда
    assert "2 ключей верхнего уровня" in текст


def test_битый_json_это_ответ_а_не_отказ(tmp_path):
    """Чаще всего человек именно это и хочет узнать."""
    файл = tmp_path / "данные.json"
    файл.write_text('{"a": 1,,}', encoding="utf-8")

    ответ = fp.file_processor({"file_path": str(файл)})

    assert "не валидный JSON" in ответ
    assert "строка" in ответ


def test_пустой_файл_назван_пустым(tmp_path):
    файл = tmp_path / "пусто.txt"
    файл.write_text("", encoding="utf-8")

    assert "пуст" in fp.file_processor({"file_path": str(файл)})


def test_неизвестный_формат_перечисляет_умения(tmp_path):
    файл = tmp_path / "архив.zip"
    файл.write_bytes(b"PK\x03\x04")

    ответ = fp.file_processor({"file_path": str(файл)})

    assert "Не знаю, как читать" in ответ
    assert "PDF" in ответ, "человеку надо понять, что вообще умеет инструмент"


# ─── Отсутствующие пакеты ─────────────────────────────────────────────────────

def test_без_pdfplumber_названо_что_ставить(tmp_path, monkeypatch):
    """«Не могу прочитать PDF» без продолжения оставляет человека гадать."""
    import builtins
    настоящий = builtins.__import__

    def без_pdf(имя, *a, **kw):
        if имя in ("pdfplumber", "PyPDF2"):
            raise ImportError("нет пакета")
        return настоящий(имя, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", без_pdf)
    файл = tmp_path / "договор.pdf"
    файл.write_bytes(b"%PDF-1.4")

    ответ = fp.file_processor({"file_path": str(файл)})

    assert "pip install" in ответ


def test_без_python_docx_названо_что_ставить(tmp_path, monkeypatch):
    import builtins
    настоящий = builtins.__import__

    def без_docx(имя, *a, **kw):
        if имя == "docx":
            raise ImportError("нет пакета")
        return настоящий(имя, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", без_docx)
    файл = tmp_path / "письмо.docx"
    файл.write_bytes(b"PK")

    assert "python-docx" in fp.file_processor({"file_path": str(файл)})


def test_отсутствие_пакета_отключает_один_формат(tmp_path, monkeypatch):
    """Инструмент целиком при этом работать не перестаёт."""
    import builtins
    настоящий = builtins.__import__

    def без_pdf(имя, *a, **kw):
        if имя in ("pdfplumber", "PyPDF2"):
            raise ImportError("нет пакета")
        return настоящий(имя, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", без_pdf)
    текстовый = tmp_path / "заметка.txt"
    текстовый.write_text("всё ещё читаюсь", encoding="utf-8")

    assert "читаюсь" in fp.file_processor({"file_path": str(текстовый)})


# ─── Брошенный в окно файл ────────────────────────────────────────────────────

def test_без_пути_берётся_брошенный_файл(tmp_path):
    """Ради этого сценария всё и затевалось: человек бросает файл и
    спрашивает, не называя пути."""
    файл = tmp_path / "договор.txt"
    файл.write_text("условия поставки", encoding="utf-8")
    окно = _Окно(current_file=str(файл))

    ответ = fp.file_processor({}, player=окно)

    assert "поставки" in ответ


def test_без_пути_и_без_брошенного_переспрашивают():
    ответ = fp.file_processor({}, player=_Окно())

    assert "Какой файл" in ответ
    assert "перетащите" in ответ.lower()


def test_явный_путь_важнее_брошенного(tmp_path):
    брошенный = tmp_path / "старый.txt"
    брошенный.write_text("старое содержимое", encoding="utf-8")
    названный = tmp_path / "новый.txt"
    названный.write_text("новое содержимое", encoding="utf-8")

    ответ = fp.file_processor({"file_path": str(названный)},
                              player=_Окно(current_file=str(брошенный)))

    assert "новое" in ответ


def test_несуществующий_файл_назван(tmp_path):
    ответ = fp.file_processor({"file_path": str(tmp_path / "нет.txt")})
    assert "Файла нет" in ответ


def test_папка_отправляет_к_другому_инструменту(tmp_path):
    ответ = fp.file_processor({"file_path": str(tmp_path)})

    assert "папка" in ответ.lower()
    assert "files" in ответ


# ─── Картинки ─────────────────────────────────────────────────────────────────

def test_картинка_уходит_в_зрение(tmp_path, monkeypatch):
    """Модуль зрения в проекте уже есть — второй такой заводить незачем."""
    вызовы = []
    monkeypatch.setattr(fp, "_разобрать_картинку",
                        lambda путь, вопрос: вызовы.append((путь.name, вопрос)) or "на фото кот")

    файл = tmp_path / "фото.jpg"
    файл.write_bytes(b"\xff\xd8\xff")

    ответ = fp.file_processor({"file_path": str(файл), "question": "что тут"})

    assert вызовы == [("фото.jpg", "что тут")]
    assert "кот" in ответ


# ─── Окно ─────────────────────────────────────────────────────────────────────

def test_окно_принимает_перетаскивание():
    """`_on_file` был написан, но его никто не вызывал: бросить файл было некуда."""
    from ui import MainWindow

    for имя in ("dragEnterEvent", "dragMoveEvent", "dropEvent"):
        assert callable(getattr(MainWindow, имя, None)), имя


def test_окно_объявляет_приём_файлов():
    """Без setAcceptDrops Qt не доставляет события перетаскивания вовсе."""
    исходник = (_BASE / "ui.py").read_text(encoding="utf-8")
    assert "setAcceptDrops(True)" in исходник


def test_инструмент_подхвачен_реестром():
    import main

    assert main._ACTIONS.has("file_processor")


def test_описание_разводит_с_файловым_инструментом():
    описание = fp.TOOL["description"]

    assert "files" in описание, "иначе модель начнёт удалять файлы этим инструментом"
    assert "перетащил" in описание
