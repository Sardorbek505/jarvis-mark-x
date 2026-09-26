"""Удаление голосом: никогда не домашнюю папку и не без пути."""

import os

from actions.file_controller import file_controller


def test_delete_without_path_refuses():
    assert "Не удаляю" in file_controller({"action": "delete"})


def test_delete_protected_folders_refuses():
    for target in ("desktop", "home", "~", os.path.expanduser("~"), "/"):
        assert "Не удаляю" in file_controller({"action": "delete", "path": target}), target


def test_delete_regular_file_works(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert "Удалено" in file_controller({"action": "delete", "path": str(f)})
    assert not f.exists()


def test_create_file_does_not_overwrite(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("важное")
    assert "уже существует" in file_controller({"action": "create_file", "path": str(f), "content": "x"})
    assert f.read_text() == "важное"
