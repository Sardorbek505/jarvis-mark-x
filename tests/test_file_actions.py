"""Пять действий, которых у `files` не было, и одно, которое врало при поиске.

Разбор Mark-LIV показал пять вещей, которые человек просит голосом каждый
день, а у нас не делались вовсе: дописать строку в файл, спросить о файле
(размер, дата), найти, что занимает место, найти все файлы одного типа и
разложить свалку на рабочем столе по папкам.

Здесь проверяется поведение на настоящих файлах в tmp_path — и особенно то,
что каждое изменение диска обратимо: голосовой ассистент ослышивается.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from actions import file_controller as fc
from core import undo


@pytest.fixture(autouse=True)
def чистый_стек():
    undo.clear()
    yield
    undo.clear()


# ─── append: дописать, не потеряв написанное ─────────────────────────────────

def test_дописывание_не_стирает_прежнее(tmp_path):
    """`create_file` перезаписывает файл целиком. «Добавь в список молоко»
    через него означало бы список из одного молока."""
    файл = tmp_path / "список.txt"
    файл.write_text("хлеб", encoding="utf-8")

    fc.file_controller({"action": "append", "path": str(файл), "content": "молоко"})

    assert файл.read_text(encoding="utf-8") == "хлеб\nмолоко"


def test_дописывание_не_приклеивает_к_последней_строке(tmp_path):
    """Без разделителя вторая мысль срастается с концом первой в одно слово."""
    файл = tmp_path / "заметки.txt"
    файл.write_text("первое\n", encoding="utf-8")

    fc.file_controller({"action": "append", "path": str(файл), "content": "второе"})

    assert файл.read_text(encoding="utf-8") == "первое\nвторое"


def test_дописывание_создаёт_файл_которого_не_было(tmp_path):
    файл = tmp_path / "новый.txt"
    fc.file_controller({"action": "append", "path": str(файл), "content": "мысль"})
    assert файл.read_text(encoding="utf-8") == "мысль"


def test_отмена_дописывания_возвращает_прежнюю_длину(tmp_path):
    """Отмена обрезкой, а не снимком: работает с файлом любого размера,
    тогда как снимок в памяти мы отказываемся держать уже с мегабайта."""
    файл = tmp_path / "журнал.txt"
    файл.write_text("важное", encoding="utf-8")

    fc.file_controller({"action": "append", "path": str(файл), "content": "лишнее"})
    undo.undo_last()

    assert файл.read_text(encoding="utf-8") == "важное"


def test_отмена_дописывания_в_новый_файл_убирает_файл(tmp_path):
    файл = tmp_path / "случайный.txt"
    fc.file_controller({"action": "append", "path": str(файл), "content": "ой"})
    undo.undo_last()
    assert not файл.exists()


# ─── info ────────────────────────────────────────────────────────────────────

def test_о_файле_рассказывается_размер_и_дата(tmp_path):
    файл = tmp_path / "договор.pdf"
    файл.write_bytes(b"x" * 2048)

    ответ = fc.file_controller({"action": "info", "path": str(файл)})

    assert "2,0 КБ" in ответ
    assert "PDF" in ответ
    assert str(файл) in ответ


def test_о_папке_говорится_сколько_внутри(tmp_path):
    (tmp_path / "а.txt").write_text("1", encoding="utf-8")
    (tmp_path / "б.txt").write_text("2", encoding="utf-8")

    ответ = fc.file_controller({"action": "info", "path": str(tmp_path)})

    assert "папка" in ответ
    assert "2" in ответ


def test_о_несуществующем_говорится_прямо(tmp_path):
    ответ = fc.file_controller({"action": "info", "path": str(tmp_path / "нет")})
    assert "не найдено" in ответ.lower()


def test_размер_округляется_до_понятного():
    assert fc._размер(512) == "512 Б"
    assert fc._размер(1024) == "1,0 КБ"
    assert fc._размер(1536 * 1024) == "1,5 МБ"
    assert fc._размер(3 * 1024 ** 3) == "3,0 ГБ"


# ─── largest: что занимает место ─────────────────────────────────────────────

def test_самые_большие_идут_первыми(tmp_path):
    (tmp_path / "мелочь.txt").write_bytes(b"x" * 10)
    (tmp_path / "громадина.iso").write_bytes(b"x" * 9000)
    (tmp_path / "среднее.bin").write_bytes(b"x" * 500)

    ответ = fc.file_controller({"action": "largest", "path": str(tmp_path)})

    assert ответ.index("громадина.iso") < ответ.index("среднее.bin") < ответ.index("мелочь.txt")


def test_самые_большие_ищутся_и_в_подпапках(tmp_path):
    (tmp_path / "глубоко").mkdir()
    (tmp_path / "глубоко" / "клад.bin").write_bytes(b"x" * 4096)

    ответ = fc.file_controller({"action": "largest", "path": str(tmp_path)})

    assert "клад.bin" in ответ


def test_слишком_долгий_обход_честно_прерывается(tmp_path, monkeypatch):
    """Домашняя папка легко содержит сотни тысяч файлов. Молчать минуту
    хуже, чем ответить по первым пятидесяти тысячам и сказать об этом."""
    monkeypatch.setattr(fc, "_ПРЕДЕЛ_ОБХОДА", 3)
    for i in range(10):
        (tmp_path / f"ф{i}.txt").write_text("x", encoding="utf-8")

    файлы, прервано = fc._обойти(tmp_path)

    assert прервано is True
    assert len(файлы) == 3
    assert "Обход остановлен" in fc.file_controller(
        {"action": "largest", "path": str(tmp_path)})


# ─── find по расширению ──────────────────────────────────────────────────────

def test_поиск_по_расширению_не_ловит_похожие_имена(tmp_path):
    """`*pdf*` находил и папку «pdf-скрипты», и файл «pdf_инструкция.txt» —
    всё, кроме того, что просили."""
    (tmp_path / "договор.pdf").write_text("1", encoding="utf-8")
    (tmp_path / "pdf_инструкция.txt").write_text("2", encoding="utf-8")
    (tmp_path / "pdf-скрипты").mkdir()

    ответ = fc.file_controller({"action": "find", "path": str(tmp_path), "extension": "pdf"})

    assert "договор.pdf" in ответ
    assert "инструкция" not in ответ
    assert "скрипты" not in ответ


def test_расширение_понимается_с_точкой_и_звёздочкой(tmp_path):
    (tmp_path / "песня.mp3").write_text("1", encoding="utf-8")
    for написание in ("mp3", ".mp3", "*.mp3", "MP3"):
        ответ = fc.file_controller(
            {"action": "find", "path": str(tmp_path), "extension": написание})
        assert "песня.mp3" in ответ, написание


def test_имя_и_расширение_сужают_друг_друга(tmp_path):
    (tmp_path / "отчёт_2024.pdf").write_text("1", encoding="utf-8")
    (tmp_path / "отчёт_2024.docx").write_text("2", encoding="utf-8")
    (tmp_path / "смета.pdf").write_text("3", encoding="utf-8")

    ответ = fc.file_controller(
        {"action": "find", "path": str(tmp_path), "name": "отчёт", "extension": "pdf"})

    assert "отчёт_2024.pdf" in ответ
    assert "docx" not in ответ
    assert "смета" not in ответ


def test_поиск_без_имени_и_расширения_переспрашивает(tmp_path):
    """Иначе `*` вернул бы первые десять файлов подряд как «найденные»."""
    (tmp_path / "что-то.txt").write_text("1", encoding="utf-8")
    ответ = fc.file_controller({"action": "find", "path": str(tmp_path)})
    assert "?" in ответ


def test_поиск_находит_только_файлы(tmp_path):
    (tmp_path / "отчёты").mkdir()
    ответ = fc.file_controller({"action": "find", "path": str(tmp_path), "name": "отчёт"})
    assert "не найдены" in ответ


# ─── organize: раскладка ─────────────────────────────────────────────────────

def _завалить(папка: Path) -> None:
    for имя in ("снимок.png", "фото.jpg", "договор.pdf", "песня.mp3",
                "кино.mkv", "архив.zip", "непонятное.qqq"):
        (папка / имя).write_text("x", encoding="utf-8")


def test_раскладка_разносит_по_типам(tmp_path):
    _завалить(tmp_path)

    fc.file_controller({"action": "organize", "path": str(tmp_path)})

    assert (tmp_path / "Картинки" / "снимок.png").exists()
    assert (tmp_path / "Картинки" / "фото.jpg").exists()
    assert (tmp_path / "Документы" / "договор.pdf").exists()
    assert (tmp_path / "Музыка" / "песня.mp3").exists()
    assert (tmp_path / "Видео" / "кино.mkv").exists()
    assert (tmp_path / "Архивы" / "архив.zip").exists()


def test_незнакомое_расширение_остаётся_на_месте(tmp_path):
    """«Прочее» — папка, куда человек складывает то, чего не понял сам, а не
    то, чего не поняли мы. Трогать такое без спроса — терять нужное."""
    _завалить(tmp_path)

    ответ = fc.file_controller({"action": "organize", "path": str(tmp_path)})

    assert (tmp_path / "непонятное.qqq").exists()
    assert "оставил на месте: 1" in ответ


def test_ярлыки_не_переносятся(tmp_path):
    """Перенесённый ярлык ломает привычку открывать программу с рабочего
    стола — ровно то, ради чего человек его туда и положил."""
    for имя in ("игра.lnk", "сайт.url", "программа.desktop"):
        (tmp_path / имя).write_text("x", encoding="utf-8")

    fc.file_controller({"action": "organize", "path": str(tmp_path)})

    for имя in ("игра.lnk", "сайт.url", "программа.desktop"):
        assert (tmp_path / имя).exists()


def test_папки_не_переезжают(tmp_path):
    (tmp_path / "Проект").mkdir()
    (tmp_path / "Проект" / "внутри.png").write_text("x", encoding="utf-8")

    fc.file_controller({"action": "organize", "path": str(tmp_path)})

    assert (tmp_path / "Проект" / "внутри.png").exists()


def test_занятое_имя_не_перезаписывается(tmp_path):
    """Перезаписать — значит потерять чужой файл ради порядка."""
    (tmp_path / "Картинки").mkdir()
    (tmp_path / "Картинки" / "фото.jpg").write_text("старое", encoding="utf-8")
    (tmp_path / "фото.jpg").write_text("новое", encoding="utf-8")

    ответ = fc.file_controller({"action": "organize", "path": str(tmp_path)})

    assert (tmp_path / "Картинки" / "фото.jpg").read_text(encoding="utf-8") == "старое"
    assert (tmp_path / "фото.jpg").read_text(encoding="utf-8") == "новое"
    assert "занято" in ответ


def test_отмена_возвращает_всю_раскладку_одним_словом(tmp_path):
    """Раскладка — десятки перемещений. Отменять их по одному человек не
    станет, значит отмена должна быть одна на всю операцию."""
    _завалить(tmp_path)
    было = sorted(п.name for п in tmp_path.iterdir())

    fc.file_controller({"action": "organize", "path": str(tmp_path)})
    undo.undo_last()

    assert sorted(п.name for п in tmp_path.iterdir()) == было


def test_пустая_раскладка_не_плодит_папок(tmp_path):
    """Иначе «разбери стол» на убранном столе создаёт шесть пустых папок."""
    (tmp_path / "непонятное.qqq").write_text("x", encoding="utf-8")

    ответ = fc.file_controller({"action": "organize", "path": str(tmp_path)})

    assert [п.name for п in tmp_path.iterdir()] == ["непонятное.qqq"]
    assert "нечего" in ответ


def test_раскладка_без_пути_берёт_рабочий_стол(monkeypatch, tmp_path):
    """«Разбери у меня тут» про домашнюю папку не говорят, а разложить её
    целиком — это переезд, которого никто не просил."""
    записано = []
    monkeypatch.setitem(fc._SHORTCUTS, "desktop", str(tmp_path))
    monkeypatch.setattr(fc, "_разложить", lambda корень, player=None: записано.append(корень))

    fc.file_controller({"action": "organize"})

    assert записано == [tmp_path]
