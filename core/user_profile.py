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

    def __init__(self, base_dir: Path):
        self.profile_path = base_dir / "config" / "user_profile.json"
        self.profile = self._load_profile()

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
        """Обновляет идентификационные данные"""
        if name:
            self.profile["identity"]["name"] = name
        if city:
            self.profile["identity"]["city"] = city
        self._save_profile()

    def update_preference(self, key: str, value: any):
        """Обновляет предпочтение"""
        if key in self.profile["preferences"]:
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
        """Получает предпочтение"""
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

        identity = self.profile["identity"]
        if identity.get("creator"):
            parts.append(f"Создатель: {identity['creator']}")
        if identity.get("name"):
            parts.append(f"Имя пользователя: {identity['name']}")
        if identity.get("city"):
            parts.append(f"Город: {identity['city']}")

        preferences = self.profile["preferences"]
        prefs = []
        if preferences.get("favorite_comedy"):
            prefs.append(f"Любимая комедия: {preferences['favorite_comedy']}")
        if preferences.get("favorite_music"):
            prefs.append(f"Любимая музыка: {preferences['favorite_music']}")
        if preferences.get("favorite_movie"):
            prefs.append(f"Любимый фильм: {preferences['favorite_movie']}")
        if preferences.get("music_genres"):
            prefs.append(f"Жанры музыки: {', '.join(preferences['music_genres'])}")
        if preferences.get("movie_genres"):
            prefs.append(f"Жанры фильмов: {', '.join(preferences['movie_genres'])}")

        if prefs:
            parts.append("Предпочтения:\n" + "\n".join(f"  - {p}" for p in prefs))

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
