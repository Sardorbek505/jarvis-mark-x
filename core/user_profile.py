"""
Профиль пользователя
Хранит предпочтения, историю, контекст для инициативных действий
"""

import json
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime

from core.storage import atomic_write_json, safe_read_json


class UserProfile:
    """Управляет профилем пользователя для персонализации"""

    def __init__(self, base_dir: Path, memory=None):
        self.profile_path = base_dir / "config" / "user_profile.json"
        self._mem = memory
        self.profile = self._load_profile()
        self._move_facts_to_memory()

    # Имя, город и вкусы — это факты о человеке, им место в общей памяти
    # (memory/memory_manager.py): оттуда они идут в промпт, видны в «Обо мне»
    # и уезжают боту. Здесь раньше жила их вторая копия — на сервер не
    # попадала и расходилась с памятью. Тут остаётся только рабочее
    # состояние: чем занят сейчас, последние фильмы и команды.
    _IDENTITY = ("name", "city")
    _PREFS = ("favorite_comedy", "favorite_music", "favorite_movie", "music_genres", "movie_genres",
              "work_style")

    def _memory(self):
        if self._mem is None:
            from memory import memory_manager
            self._mem = memory_manager
        return self._mem

    def _remember(self, category: str, key: str, value) -> bool:
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value if v)
        if value in (None, ""):
            return False
        try:
            self._memory().update_memory({category: {key: value}})
            return True
        except Exception as e:
            print(f"[UserProfile] Память недоступна: {e}")
            return False

    def _move_facts_to_memory(self):
        """Один раз: старые имя/город/вкусы из user_profile.json — в память."""
        moved = False
        for key in self._IDENTITY:
            if self._remember("identity", key, self.profile.get("identity", {}).get(key)):
                self.profile["identity"][key] = None
                moved = True
        prefs = self.profile.get("preferences", {})
        for key in self._PREFS:
            if self._remember("preferences", key, prefs.get(key)):
                prefs[key] = [] if isinstance(prefs.get(key), list) else None
                moved = True
        if moved:
            self._save_profile()

    def _load_profile(self) -> Dict:
        """Загружает профиль из файла"""
        if self.profile_path.exists():
            loaded = safe_read_json(self.profile_path, default=None)
            if loaded:
                return loaded

        # Профиль по умолчанию
        return {
            "identity": {
                "name": None,
                "creator": "Sardarbek",  # Создатель Джарвиса
                "city": None,
                "timezone": None
            },
            "preferences": {
                "favorite_comedy": None,
                "favorite_music": None,
                "favorite_movie": None,
                "music_genres": [],
                "movie_genres": [],
                "work_style": None,  # focused, creative, systematic
                "break_duration": 15,  # минут
            },
            "context": {
                "current_activity": None,
                "last_activity": None,
                "last_emotion": None,
                "session_start": None,
                "interaction_count": 0
            },
            "history": {
                "recent_movies": [],
                "recent_music": [],
                "recent_commands": []
            }
        }

    def _save_profile(self):
        """Сохраняет профиль в файл (атомарно)"""
        try:
            atomic_write_json(self.profile_path, self.profile)
        except Exception as e:
            print(f"[UserProfile] Ошибка сохранения: {e}")

    def update_identity(self, name: Optional[str] = None, city: Optional[str] = None):
        """Имя и город — в общую память."""
        self._remember("identity", "name", name)
        self._remember("identity", "city", city)

    def update_preference(self, key: str, value: any):
        """Вкусы — в общую память; break_duration и т.п. — настройки, остаются здесь."""
        if key in self._PREFS:
            self._remember("preferences", key, value)
        elif key in self.profile["preferences"]:
            self.profile["preferences"][key] = value
            self._save_profile()

    def add_to_history(self, category: str, item: str, max_items: int = 10):
        """Добавляет элемент в историю с ограничением размера"""
        if category not in self.profile["history"]:
            self.profile["history"][category] = []

        history = self.profile["history"][category]

        # Удаляем если уже есть (чтобы переместить в начало)
        if item in history:
            history.remove(item)

        # Добавляем в начало
        history.insert(0, item)

        # Ограничиваем размер
        if len(history) > max_items:
            history = history[:max_items]

        self.profile["history"][category] = history
        self._save_profile()

    def update_context(self, activity: Optional[str] = None, emotion: Optional[str] = None):
        """Обновляет контекст текущей сессии"""
        if activity:
            self.profile["context"]["last_activity"] = self.profile["context"]["current_activity"]
            self.profile["context"]["current_activity"] = activity

        if emotion:
            self.profile["context"]["last_emotion"] = emotion

        if not self.profile["context"]["session_start"]:
            self.profile["context"]["session_start"] = datetime.now().isoformat()

        self.profile["context"]["interaction_count"] += 1
        self._save_profile()

    def get_preference(self, key: str, default: any = None) -> any:
        """Получает предпочтение (вкусы — из общей памяти)."""
        if key in self._PREFS:
            try:
                val = self._memory().load_memory().get("preferences", {}).get(key)
                val = val.get("value") if isinstance(val, dict) else val
                if val not in (None, ""):
                    return val
            except Exception:
                pass
        return self.profile["preferences"].get(key, default)

    def get_recent(self, category: str, limit: int = 5) -> List[str]:
        """Получает последние элементы из истории"""
        history = self.profile["history"].get(category, [])
        return history[:limit]

    def get_context(self) -> Dict:
        """Получает текущий контекст"""
        return self.profile["context"]

    def get_full_profile(self) -> Dict:
        """Получает полный профиль для контекста"""
        return self.profile

    def format_for_prompt(self) -> str:
        """Форматирует профиль для включения в prompt"""
        parts = []

        # Имя, город и вкусы приходят в промпт из общей памяти — здесь не повторяем.
        if self.profile["identity"].get("creator"):
            parts.append(f"Создатель: {self.profile['identity']['creator']}")

        context = self.profile["context"]
        if context.get("current_activity"):
            parts.append(f"Текущая активность: {context['current_activity']}")
        if context.get("last_emotion"):
            parts.append(f"Последняя эмоция: {context['last_emotion']}")

        if parts:
            return "[ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ]\n" + "\n".join(parts) + "\n\n"
        return ""

    def learn_from_interaction(self, command: str, action: str, result: str):
        """
        Изучает из взаимодействия пользователя

        Args:
            command: Команда пользователя
            action: Выполненное действие
            result: Результат (success/failed)
        """
        # Добавляем в историю команд
        self.profile["history"]["recent_commands"].append({
            "command": command,
            "action": action,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })

        # Ограничиваем историю команд
        if len(self.profile["history"]["recent_commands"]) > 50:
            self.profile["history"]["recent_commands"] = self.profile["history"]["recent_commands"][-50:]

        self._save_profile()


# Тестирование
if __name__ == "__main__":
    from pathlib import Path

    profile = UserProfile(Path(__file__).parent.parent)

    # Тест обновления
    profile.update_identity(name="Александр", city="Москва")
    profile.update_preference("favorite_comedy", "КВН")
    profile.update_preference("favorite_music", "jazz")
    profile.add_to_history("recent_movies", "Интерстеллар")
    profile.update_context(activity="watching_movie", emotion="happy")

    print("Полный профиль:")
    print(json.dumps(profile.get_full_profile(), ensure_ascii=False, indent=2))

    print("\n\nДля prompt:")
    print(profile.format_for_prompt())
