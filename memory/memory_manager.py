"""
ДЖАРВИС — Менеджер долгосрочной памяти
"""

import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_MEMORY_FILE = _BASE / "memory" / "data.json"

# Гарантируем что core/ доступен (memory/ соседний с core/)
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core.storage import atomic_write_json, safe_read_json


def load_memory() -> dict:
    return safe_read_json(_MEMORY_FILE, default={})


def update_memory(patch: dict):
    mem = load_memory()
    for category, items in patch.items():
        if category not in mem:
            mem[category] = {}
        for key, val in items.items():
            mem[category][key] = val
    atomic_write_json(_MEMORY_FILE, mem)


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
