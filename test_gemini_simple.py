"""Простой тест Gemini API"""
import json
from pathlib import Path
import google.genai as genai

# Загрузка API ключа
_BASE = Path(__file__).parent
api_key_file = _BASE / "config" / "api_keys.json"

try:
    with open(api_key_file, "r", encoding="utf-8") as f:
        api_key = json.load(f).get("gemini_api_key", "")
except Exception as e:
    print(f"Error loading API key: {e}")
    api_key = ""

print(f"API Key loaded: {api_key[:20]}..." if api_key else "No API key")

# Тест Gemini API
try:
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Say hello in Russian"
    )
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
