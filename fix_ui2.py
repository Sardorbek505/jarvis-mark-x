#!/usr/bin/env python3
"""Полная замена метода wait_for_api_key"""

file_path = "D:/jarvis-ru/jarvis-ru/ui.py"

with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Находим начало метода
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if "def wait_for_api_key(self):" in line:
        start_idx = i
    if start_idx and "def _show_overlay" in line:
        end_idx = i
        break

if start_idx and end_idx:
    # Новый метод
    new_method = '''    def wait_for_api_key(self):
        """Блокирует поток до получения API-ключа. Пропускает если ключ уже есть."""
        import threading
        import json
        from pathlib import Path

        # Проверяем, существует ли файл с ключом
        config_path = Path(__file__).parent / "config" / "api_keys.json"
        reason = "init"
        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    api_key = data.get("gemini_api_key")
                    if api_key:
                        # Ключ есть - пропускаем без проверки API для быстрого запуска
                        print("[UI] API ключ найден, пропускаем инициализацию...")
                        return None
        except Exception as e:
            print(f"[UI] Ошибка чтения ключа: {e}")
            pass

        # Ключа нет или файл повреждён — показываем оверлей
        self._key_ready = threading.Event()
        self._setup_reason = reason
        QTimer.singleShot(0, lambda: self._show_overlay(reason))
        self._key_ready.wait()
        return reason

'''
    
    # Заменяем метод
    lines[start_idx:end_idx] = [new_method]
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    print("Метод wait_for_api_key обновлён!")
else:
    print("Не удалось найти метод")
