"""
ДЖАРВИС — Менеджер долгосрочной памяти
"""

import sys
import threading
from datetime import datetime
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent

# Гарантируем что core/ доступен (memory/ соседний с core/)
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core.paths import get_data_root
from core.storage import atomic_write_json, safe_read_json

# В .exe — %APPDATA%/JARVIS/memory (см. get_data_root), из исходников — как раньше.
_MEMORY_FILE = get_data_root() / "memory" / "data.json"


_LOCK = threading.Lock()
_PER_CATEGORY = 25          # фактов в категории: старые вытесняются новыми
_PROMPT_CHARS = 1800        # сколько памяти идёт в каждую сессию


def load_memory() -> dict:
    return safe_read_json(_MEMORY_FILE, default={})


def _stamp(val) -> str:
    return val.get("updated", "") if isinstance(val, dict) else ""


def update_memory(patch: dict):
    """Добавить/обновить факты. Раньше память только росла — без предела, и
    вся уходила в каждую сессию. Теперь у факта есть время, в категории —
    не больше _PER_CATEGORY самых свежих. Под замком: голос и Telegram-бот
    писали одновременно и затирали друг друга."""
    now = datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        mem = load_memory()
        for category, items in patch.items():
            cat = mem.setdefault(category, {})
            for key, val in items.items():
                if val in (None, ""):
                    continue
                cat[key] = {"value": val.get("value", val) if isinstance(val, dict) else val,
                            "updated": now}
            if len(cat) > _PER_CATEGORY:
                keep = sorted(cat, key=lambda k: _stamp(cat[k]), reverse=True)[:_PER_CATEGORY]
                mem[category] = {k: cat[k] for k in cat if k in keep}
        atomic_write_json(_MEMORY_FILE, mem)


def forget(category: str, key: str) -> bool:
    with _LOCK:
        mem = load_memory()
        if key in mem.get(category, {}):
            del mem[category][key]
            atomic_write_json(_MEMORY_FILE, mem)
            return True
    return False


_CAT_RU = {
    "identity": "Личность", "preferences": "Предпочтения", "projects": "Проекты",
    "relationships": "Отношения", "wishes": "Планы и желания", "notes": "Заметки",
}


def format_memory_for_prompt(memory: dict) -> str:
    """Самое важное и свежее — в пределах _PROMPT_CHARS: сначала личность,
    внутри категории — новые факты первыми."""
    if not memory:
        return ""
    lines = ["[ДОЛГОСРОЧНАЯ ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ]"]
    size = len(lines[0])
    order = sorted(memory, key=lambda c: (c != "identity", list(_CAT_RU).index(c) if c in _CAT_RU else 99))
    for category in order:
        items = memory[category] or {}
        if not items:
            continue
        head = f"\n{_CAT_RU.get(category, category)}:"
        for n, key in enumerate(sorted(items, key=lambda k: _stamp(items[k]), reverse=True)):
            val = items[key]
            line = f"  {key}: {val.get('value', val) if isinstance(val, dict) else val}"
            add = (head + "\n" if n == 0 else "") + line
            if size + len(add) > _PROMPT_CHARS:
                lines.append("")
                return "\n".join(lines)
            if n == 0:
                lines.append(head)
            lines.append(line)
            size += len(add)
    lines.append("")
    return "\n".join(lines)
