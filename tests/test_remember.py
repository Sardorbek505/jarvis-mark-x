"""«Запомни это»: снимок → суть, детали, ссылки → заметка в Obsidian со
снимком рядом; ответ Gemini в мусоре или без снимка — честная ошибка."""
from datetime import datetime

import pytest

from core import remember as R

ANSWER = ('{"title": "Заказ Ozon 4815", "summary": "Заказ наушников, доставка в пункт выдачи.",'
          ' "facts": ["Номер заказа 4815-162", "Доставка 3 октября"], "links": ["ozon.ru/my/orders"],'
          ' "tags": ["Покупки", "заказ"]}')


def _call(tmp_path, answer=ANSWER, jpeg=b"\xff\xd8jpeg", **p):
    asked = []
    res = R.remember_screen(p, capture=lambda src: jpeg, ask=lambda j, f: asked.append(f) or answer,
                            vault=lambda: tmp_path, window=lambda: "Ozon — Мои заказы",
                            now=lambda: datetime(2026, 9, 27, 14, 5))
    return res, asked


def test_saves_note_with_details_and_image(tmp_path):
    res, asked = _call(tmp_path, focus="номер заказа")
    assert asked == ["номер заказа"]
    assert "«Заказ Ozon 4815»" in res and "4815-162" in res
    note = tmp_path / "Запомнил" / "Заказ Ozon 4815.md"
    text = note.read_text(encoding="utf-8")
    assert "# Заказ Ozon 4815" in text and "- Номер заказа 4815-162" in text and "- ozon.ru/my/orders" in text
    assert "tags: [запомнил, покупки, заказ]" in text and "Просили запомнить: номер заказа" in text
    assert "Окно: Ozon — Мои заказы" in text
    img = next((tmp_path / "Запомнил" / "attachments").iterdir())
    assert img.read_bytes() == b"\xff\xd8jpeg" and f"![[attachments/{img.name}]]" in text
    _call(tmp_path)                                                    # то же ещё раз — не затирает
    assert (tmp_path / "Запомнил" / "Заказ Ozon 4815 (2).md").exists()


def test_without_image(tmp_path):
    _call(tmp_path, keep_image=False)
    assert "![[" not in (tmp_path / "Запомнил" / "Заказ Ozon 4815.md").read_text(encoding="utf-8")
    assert not (tmp_path / "Запомнил" / "attachments").exists()


@pytest.mark.parametrize("answer", ["не json", '{"title": "", "summary": ""}'])
def test_bad_answer_is_honest(tmp_path, answer):
    res, _ = _call(tmp_path, answer=answer)
    assert res.startswith("Снимок сделал, но разобрать не вышло")
    assert not (tmp_path / "Запомнил").exists() or not list((tmp_path / "Запомнил").glob("*.md"))


def test_no_screenshot(tmp_path):
    res, asked = _call(tmp_path, jpeg=None)
    assert res == "Не получилось сделать снимок экрана." and asked == []


def test_parse_cleans_up():
    d = R.parse('Вот: {"title": "  Очень\\nдлинный   заголовок ", "summary": "Суть", "facts": ["", " a  b "],'
                ' "tags": ["Работа/Учёба", "x"]}')
    assert d["title"] == "Очень длинный заголовок" and d["facts"] == ["a b"] and d["tags"] == ["работа_учёба", "x"]
