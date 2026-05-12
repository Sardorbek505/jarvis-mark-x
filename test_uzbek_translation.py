"""Тест перевода на узбекский язык"""
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

# Проверка что узбекский включен
print("=== Language Check ===")
languages = manager.get_available_languages()
for lang in languages:
    status = "[ON]" if lang.get("enabled", False) else "[OFF]"
    print(f"{status} {lang['name']} ({lang['code']})")

# Тест перевода
print("\n=== Uzbek Translation Test ===")
test_text = "Privet, kak dela?"
print(f"Source text (Russian): {test_text}")

translation = manager.translate(test_text, target_lang="uz", source_lang="ru")
print(f"Translation (Uzbek): {translation}")

# Ещё один тест
test_text2 = "Ya hochu perevesti eto predlozheniye na uzbekskiy yazyk"
print(f"\nSource text: {test_text2}")
translation2 = manager.translate(test_text2, target_lang="uz", source_lang="ru")
print(f"Translation: {translation2}")

print("\n=== Translation History ===")
recent = manager.get_recent_translations(5)
for i, entry in enumerate(recent, 1):
    print(f"{i}. {entry['source_text']} -> {entry['target_text']}")
