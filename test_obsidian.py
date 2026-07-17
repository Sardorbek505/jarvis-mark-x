"""Автономный тест интеграции Obsidian (без Gemini/микрофона)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from actions.obsidian import obsidian_action, _config  # noqa: E402

print("vault:", _config()["vault_path"])
print()

# 1. write
print("[write]     ", obsidian_action({
    "action": "write",
    "title": "Идея для Zapis",
    "content": "Добавить white-label онбординг бизнесов прямо из UI кабинета.",
    "folder": "Knowledge",
}))

# 2. append_daily
print("[daily]     ", obsidian_action({
    "action": "append_daily",
    "content": "Подключил Obsidian к Джарвису как базу знаний.",
}))

# 3. search
print("[search]    ", obsidian_action({"action": "search", "query": "white-label"}))

# 4. read
print("[read]      ", obsidian_action({"action": "read", "title": "Идея для Zapis"}))

# 5. list
print("[list]      ", obsidian_action({"action": "list", "folder": "Knowledge"}))

# 6. bad action
print("[bad]       ", obsidian_action({"action": "погладить кота"}))

print("\nOK: интеграция работает.")
