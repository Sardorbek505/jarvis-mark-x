"""Файловые операции обязаны уметь откатываться, а удаление — не быть вечным.

`file_controller` перемещал, переименовывал и удалял без следа: `p.unlink()`
мимо корзины и ни одной записи об отмене. Ослышался ассистент — файла нет.

Проверяется поведение целиком, на настоящих файлах в tmp_path: важно не то,
что отмена зарегистрирована, а то, что после неё файл действительно на месте.
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


def test_перемещение_возвращается_на_место(tmp_path):
    исходник = tmp_path / "отчёт.txt"
    исходник.write_text("данные", encoding="utf-8")
    папка = tmp_path / "Документы"
    папка.mkdir()

    fc.file_controller({"action": "move", "path": str(исходник), "destination": str(папка)})
    assert not исходник.exists()
    assert (папка / "отчёт.txt").exists()

    undo.undo_last()
    assert исходник.exists()
    assert исходник.read_text(encoding="utf-8") == "данные"
    assert not (папка / "отчёт.txt").exists()


def test_переименование_возвращает_прежнее_имя(tmp_path):
    файл = tmp_path / "старое.txt"
    файл.write_text("x", encoding="utf-8")

    fc.file_controller({"action": "rename", "path": str(файл), "new_name": "новое.txt"})
    assert (tmp_path / "новое.txt").exists()

    undo.undo_last()
    assert файл.exists()
    assert not (tmp_path / "новое.txt").exists()


def test_созданный_файл_убирается_отменой(tmp_path):
    новый = tmp_path / "заметка.txt"
    fc.file_controller({"action": "create_file", "path": str(новый), "content": "текст"})
    assert новый.exists()

    undo.undo_last()
    assert not новый.exists()


def test_перезапись_возвращает_прежнее_содержимое(tmp_path):
    файл = tmp_path / "важное.txt"
    файл.write_text("то, что было", encoding="utf-8")

    fc.file_controller({"action": "create_file", "path": str(файл), "content": "то, что стало"})
    assert файл.read_text(encoding="utf-8") == "то, что стало"

    undo.undo_last()
    assert файл.read_text(encoding="utf-8") == "то, что было"


def test_копия_убирается_а_оригинал_остаётся(tmp_path):
    исходник = tmp_path / "исходник.txt"
    исходник.write_text("данные", encoding="utf-8")
    папка = tmp_path / "Копии"
    папка.mkdir()

    fc.file_controller({"action": "copy", "path": str(исходник), "destination": str(папка)})
    assert (папка / "исходник.txt").exists()

    undo.undo_last()
    assert not (папка / "исходник.txt").exists()
    assert исходник.exists(), "обратное к копированию — убрать копию, не оригинал"


def test_созданная_папка_убирается_только_пустой(tmp_path):
    папка = tmp_path / "Новая"
    fc.file_controller({"action": "create_folder", "path": str(папка)})
    assert папка.is_dir()

    (папка / "чужое.txt").write_text("не трогать", encoding="utf-8")
    ответ = undo.undo_last()

    assert папка.is_dir(), "в папку успели что-то положить — забирать её с собой нельзя"
    assert (папка / "чужое.txt").exists()
    assert "не пуста" in ответ


def test_существовавшая_папка_в_стек_не_попадает(tmp_path):
    папка = tmp_path / "Уже была"
    папка.mkdir()

    fc.file_controller({"action": "create_folder", "path": str(папка)})

    assert undo.can_undo() is False, "мы её не создавали — и убирать не наше дело"


def test_удаление_уходит_в_корзину(tmp_path, monkeypatch):
    файл = tmp_path / "ненужное.txt"
    файл.write_text("x", encoding="utf-8")
    корзина = []

    monkeypatch.setattr(fc, "_to_trash", lambda p: корзина.append(p) or True)
    ответ = fc.file_controller({"action": "delete", "path": str(файл)})

    assert корзина == [файл]
    assert "корзину" in ответ
    assert файл.exists(), "в корзину — значит, сами файл не трогаем"


def test_без_корзины_удаление_названо_безвозвратным(tmp_path, monkeypatch):
    """Молчание здесь означало бы, что человек считает файл восстановимым."""
    файл = tmp_path / "ненужное.txt"
    файл.write_text("x", encoding="utf-8")

    monkeypatch.setattr(fc, "_to_trash", lambda p: False)
    ответ = fc.file_controller({"action": "delete", "path": str(файл)})

    assert not файл.exists()
    assert "безвозвратно" in ответ


def test_удаление_несуществующего_не_врёт(tmp_path):
    ответ = fc.file_controller({"action": "delete", "path": str(tmp_path / "нет-такого.txt")})
    assert "Не найдено" in ответ


def test_большой_файл_в_память_ради_отмены_не_тянем(tmp_path):
    файл = tmp_path / "лог.txt"
    файл.write_text("я" * (undo.MAX_SNAPSHOT_BYTES + 10), encoding="utf-8")

    fc.file_controller({"action": "create_file", "path": str(файл), "content": "коротко"})

    assert undo.can_undo() is False, "держать в сессии мегабайты ради отмены — не та цена"
