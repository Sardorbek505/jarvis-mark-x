"""«Новая квартира» — это имя заметки, а не «самая новая заметка».

Было: слова «новая», «последн», «свеж» проверялись раньше имени, и
«удали заметку Новая квартира» удаляла самую свежую заметку."""
import os
import time

from actions import obsidian as ob


def _note(vault, name, age):
    p = vault / f"{name}.md"
    p.write_text("x", encoding="utf-8")
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def test_имя_важнее_слова_новая(tmp_path):
    flat = _note(tmp_path, "Новая квартира", age=3600)
    _note(tmp_path, "Дневник работы", age=10)
    assert ob._find("Новая квартира", tmp_path) == flat
    assert ob._find("новая квартира", tmp_path) == flat


def test_последняя_заметка_по_прежнему_работает(tmp_path):
    _note(tmp_path, "Старая", age=3600)
    fresh = _note(tmp_path, "Дневник работы", age=10)
    assert ob._find("последнюю", tmp_path) == fresh
    assert ob._find("", tmp_path) == fresh


def test_неизвестное_имя_не_подменяется_свежей(tmp_path):
    _note(tmp_path, "Дневник работы", age=10)
    assert ob._find("Рецепты", tmp_path) is None
