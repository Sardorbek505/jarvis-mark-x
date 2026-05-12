"""Тест перевода на русский язык"""
import json
from pathlib import Path
from core.translation_manager import TranslationManager

# Загрузка API ключа
_BASE = Path(__file__).parent
api_key_file = _BASE / "config" / "api_keys.json"

try:
    with open(api_key_file, "r", encoding="utf-8") as f:
        api_key = json.load(f).get("gemini_api_key", "")
except Exception as e:
    print(f"Error loading API key: {e}")
    api_key = ""

# Инициализация менеджера переводов
manager = TranslationManager(api_key)

# Проверка языков
print("=== Language Check ===")
languages = manager.get_available_languages()
for lang in languages:
    status = "[ON]" if lang.get("enabled", False) else "[OFF]"
    print(f"{status} {lang['name']} ({lang['code']})")

# Тест перевода на русский
print("\n=== Russian Translation Test ===")
test_text = "Hello, how are you?"
print(f"Source text (English): {test_text}")

translation = manager.translate(test_text, target_lang="ru", source_lang="en")
print(f"Translation (Russian): {translation}")

# Тест с узбекского на русский
test_text2 = "Salom, qaleysan?"
print(f"\nSource text (Uzbek): {test_text2}")
translation2 = manager.translate(test_text2, target_lang="ru", source_lang="uz")
print(f"Translation (Russian): {translation2}")

print("\n=== Translation History ===")
recent = manager.get_recent_translations(5)
for i, entry in enumerate(recent, 1):
    print(f"{i}. {entry['source_text']} -> {entry['target_text']}")
