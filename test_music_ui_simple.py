#!/usr/bin/env python3
"""Простой тест проверки логики music player без UI automation"""

import sys
import os

# Добавляем текущую директорию в path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from actions.music_player import (
    _is_spotify_installed,
    _HAS_PYAUTOGUI,
    _spotify_search_track_uri,
)

print("=== Проверка логики Music Player ===")
print()

print(f"Spotify установлен: {_is_spotify_installed()}")
print(f"pyautogui доступен: {_HAS_PYAUTOGUI}")
print()

# Тестируем поиск через API
test_queries = [
    "Imagine Dragons",
    "Queen Bohemian Rhapsody",
    "Metallica",
]

print("Тестирование поиска через API:")
print("-" * 50)
for query in test_queries:
    uri = _spotify_search_track_uri(query)
    if uri:
        print(f"✅ {query} → {uri}")
    else:
        print(f"❌ {query} → не найден (API недоступен или нет результатов)")

print()
print("Логика проверена. UI automation будет использоваться если API недоступен.")
