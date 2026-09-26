"""Долгосрочная память голосового Джарвиса: не растёт без предела, в сессию
уходит самое важное."""
import memory.memory_manager as mm


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "_MEMORY_FILE", tmp_path / "data.json")


def test_category_is_capped(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    for i in range(mm._PER_CATEGORY + 15):
        mm.update_memory({"notes": {f"n{i}": "факт"}})
    assert len(mm.load_memory()["notes"]) == mm._PER_CATEGORY


def test_prompt_is_bounded_and_identity_first(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    for i in range(70):
        mm.update_memory({"notes": {f"n{i}": "очень длинная заметка " * 5}})
    mm.update_memory({"identity": {"name": "Сардор"}})
    out = mm.format_memory_for_prompt(mm.load_memory())
    assert len(out) <= mm._PROMPT_CHARS + 2
    assert out.index("Сардор") < out.index("Заметки")


def test_empty_values_are_not_saved_and_forget_works(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    mm.update_memory({"identity": {"city": "", "name": "Сардор"}})
    assert "city" not in mm.load_memory()["identity"]
    assert mm.forget("identity", "name") and "name" not in mm.load_memory()["identity"]


def test_old_flat_values_still_render(tmp_path, monkeypatch):
    assert "Шымкент" in mm.format_memory_for_prompt({"identity": {"city": "Шымкент"}})
