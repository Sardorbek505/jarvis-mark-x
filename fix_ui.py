#!/usr/bin/env python3
"""Исправление ui.py для быстрого запуска"""

import re

file_path = "D:/jarvis-ru/jarvis-ru/ui.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Заменяем блок проверки API на простой возврат
old_pattern = r'if api_key:\s+# Проверяем валидность ключа через запрос к API.*?return None\s+except Exception:.+?reason = "invalid"'

new_code = '''if api_key:
                        # Ключ есть - пропускаем без проверки API для быстрого запуска
                        print("[UI] API ключ найден, пропускаем инициализацию...")
                        return None'''

content = re.sub(old_pattern, new_code, content, flags=re.DOTALL)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Исправлено!")
