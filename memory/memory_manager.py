"""
ДЖАРВИС — Менеджер долгосрочной памяти
"""

import json
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_MEMORY_FILE = _BASE / "memory" / "data.json"


def load_memory() -> dict:
    try:
        if _MEMORY_FILE.exists():
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def update_memory(patch: dict):
    mem = load_memory()
    for category, items in patch.items():
        if category not in mem:
            mem[category] = {}
        for key, val in items.items():
            mem[category][key] = val
    _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)


def format_memory_for_prompt(memory: dict) -> str:
    if not memory:
        return ""
    lines = ["[ДОЛГОСРОЧНАЯ ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ]"]
    for category, items in memory.items():
        cat_ru = {
            "identity": "Личность",
            "preferences": "Предпочтения",
            "projects": "Проекты",
            "relationships": "Отношения",
            "wishes": "Планы и желания",
            "notes": "Заметки",
        }.get(category, category)
        lines.append(f"\n{cat_ru}:")
        for key, val in items.items():
            if isinstance(val, dict):
                lines.append(f"  {key}: {val.get('value', val)}")
            else:
                lines.append(f"  {key}: {val}")
    lines.append("")
    return "\n".join(lines)
