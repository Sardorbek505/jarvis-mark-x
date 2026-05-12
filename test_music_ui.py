#!/usr/bin/env python3
"""Тест UI automation для music player"""

import sys
import os

# Добавляем текущую директорию в path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from actions.music_player import _ui_automation_search, _is_spotify_installed, _HAS_PYAUTOGUI

print("=== Тест UI automation для Music Player ===")
print()

print(f"Spotify установлен: {_is_spotify_installed()}")
print(f"pyautogui доступен: {_HAS_PYAUTOGUI}")
print()

if not _is_spotify_installed():
    print("❌ Spotify не установлен - тест невозможен")
    sys.exit(1)

if not _HAS_PYAUTOGUI:
    print("❌ pyautogui недоступен - тест невозможен")
    sys.exit(1)

# Тестируем UI automation
test_query = "Imagine Dragons"
print(f"Тестируем UI automation с запросом: {test_query}")
print()

result = _ui_automation_search(test_query, player=None)

if result:
    print(f"✅ UI automation успешен")
else:
    print(f"❌ UI automation не сработал")

print()
print("Проверьте Spotify - должен открыться и начать воспроизводить трек")
