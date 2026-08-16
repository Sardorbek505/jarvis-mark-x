"""
Модуль для перевода в реальном времени
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import google.genai as genai

from core.storage import atomic_write_json

_logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent


class TranslationPreferences:
    """Управляет предпочтениями для перевода."""
    
    def __init__(self):
        self.preferences_file = _BASE / "config" / "translation_preferences.json"
        self.preferences = self._load_preferences()
    
    def _load_preferences(self) -> Dict[str, Any]:
        """Загружает предпочтения из файла."""
        if not self.preferences_file.exists():
            return self._get_default_preferences()
        
        try:
            with open(self.preferences_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return self._get_default_preferences()
    
    def _get_default_preferences(self) -> Dict[str, Any]:
        """Возвращает предпочтения по умолчанию."""
        return {
            "target_languages": [
                {
                    "code": "en",
                    "name": "English",
                    "native_name": "English",
                    "enabled": True
                }
            ],
            "default_target_language": "en",
            "source_language": "ru",
            "learning_mode": {
                "enabled": False,
                "target_language": "en",
                "difficulty": "intermediate",
                "daily_practice": True
            },
            "translation_history": {
                "enabled": True,
                "max_entries": 100
            },
            "context_memory": {
                "enabled": True,
                "max_context_entries": 10
            },
            "auto_detect_language": True
        }
    
    def save_preferences(self) -> bool:
        """Сохраняет предпочтения в файл."""
        try:
            atomic_write_json(self.preferences_file, self.preferences)
            return True
        except Exception as exc:
            _logger.error("Не сохранил %s: %s", self.preferences_file.name, exc)
            return False
    
    def get_enabled_languages(self) -> List[Dict[str, Any]]:
        """Возвращает список включённых языков."""
        return [lang for lang in self.preferences.get("target_languages", []) if lang.get("enabled", False)]
    
    def get_default_target_language(self) -> str:
        """Возвращает целевой язык по умолчанию."""
        return self.preferences.get("default_target_language", "en")
    
    def set_default_target_language(self, lang_code: str) -> bool:
        """Устанавливает целевой язык по умолчанию."""
        self.preferences["default_target_language"] = lang_code
        return self.save_preferences()
    
    def enable_language(self, lang_code: str) -> bool:
        """Включает язык."""
        for lang in self.preferences["target_languages"]:
            if lang["code"] == lang_code:
                lang["enabled"] = True
                return self.save_preferences()
        return False
    
    def disable_language(self, lang_code: str) -> bool:
        """Отключает язык."""
        for lang in self.preferences["target_languages"]:
            if lang["code"] == lang_code:
                lang["enabled"] = False
                return self.save_preferences()
        return False
    
    def get_language_info(self, lang_code: str) -> Optional[Dict[str, Any]]:
        """Возвращает информацию о языке."""
        for lang in self.preferences["target_languages"]:
            if lang["code"] == lang_code:
                return lang
        return None
    
    def toggle_learning_mode(self, enabled: bool) -> bool:
        """Включает/выключает режим изучения."""
        self.preferences["learning_mode"]["enabled"] = enabled
        return self.save_preferences()
    
    def set_learning_target(self, lang_code: str) -> bool:
        """Устанавливает целевой язык для изучения."""
        self.preferences["learning_mode"]["target_language"] = lang_code
        return self.save_preferences()


class TranslationHistory:
    """Управляет историей переводов."""
    
    def __init__(self, preferences: TranslationPreferences):
        self.preferences = preferences
        self.history_file = _BASE / "config" / "translation_history.json"
        self.history = self._load_history()
    
    def _load_history(self) -> List[Dict[str, Any]]:
        """Загружает историю переводов."""
        if not self.history_file.exists():
            return []
        
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    
    def _save_history(self) -> bool:
        """Сохраняет историю переводов."""
        try:
            atomic_write_json(self.history_file, self.history)
            return True
        except Exception as exc:
            _logger.error("Не сохранил %s: %s", self.history_file.name, exc)
            return False
    
    def add_translation(self, source_text: str, target_text: str, 
                        source_lang: str, target_lang: str) -> bool:
        """
        Добавляет перевод в историю.
        
        Args:
            source_text: Исходный текст
            target_text: Переведённый текст
            source_lang: Исходный язык
            target_lang: Целевой язык
            
        Returns:
            True если успешно
        """
        entry = {
            "source_text": source_text,
            "target_text": target_text,
            "source_language": source_lang,
            "target_language": target_lang,
            "timestamp": datetime.now().isoformat()
        }
        
        self.history.insert(0, entry)
        
        # Ограничиваем количество записей
        max_entries = self.preferences.preferences.get("translation_history", {}).get("max_entries", 100)
        if len(self.history) > max_entries:
            self.history = self.history[:max_entries]
        
        return self._save_history()
    
    def search_history(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Ищет переводы по запросу.
        
        Args:
            query: Поисковый запрос
            limit: Максимальное количество результатов
            
        Returns:
            Список найденных переводов
        """
        query_lower = query.lower()
        results = []
        
        for entry in self.history:
            if (query_lower in entry["source_text"].lower() or 
                query_lower in entry["target_text"].lower()):
                results.append(entry)
            
            if len(results) >= limit:
                break
        
        return results
    
    def get_recent_translations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Возвращает последние переводы."""
        return self.history[:limit]


class ContextMemory:
    """Управляет контекстной памятью для перевода."""
    
    def __init__(self, preferences: TranslationPreferences):
        self.preferences = preferences
        self.context_file = _BASE / "config" / "translation_context.json"
        self.context = self._load_context()
    
    def _load_context(self) -> List[Dict[str, Any]]:
        """Загружает контекстную память."""
        if not self.context_file.exists():
            return []
        
        try:
            with open(self.context_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    
    def _save_context(self) -> bool:
        """Сохраняет контекстную память."""
        try:
            atomic_write_json(self.context_file, self.context)
            return True
        except Exception as exc:
            _logger.error("Не сохранил %s: %s", self.context_file.name, exc)
            return False
    
    def add_context(self, text: str, translation: str, lang: str) -> bool:
        """
        Добавляет контекст.
        
        Args:
            text: Текст
            translation: Перевод
            lang: Язык
            
        Returns:
            True если успешно
        """
        entry = {
            "text": text,
            "translation": translation,
            "language": lang,
            "timestamp": datetime.now().isoformat()
        }
        
        self.context.insert(0, entry)
        
        # Ограничиваем количество записей
        max_entries = self.preferences.preferences.get("context_memory", {}).get("max_context_entries", 10)
        if len(self.context) > max_entries:
            self.context = self.context[:max_entries]
        
        return self._save_context()
    
    def get_recent_context(self, lang: str, limit: int = 5) -> List[str]:
        """Возвращает недавний контекст для языка."""
        lang_context = [c for c in self.context if c["language"] == lang]
        return [c["translation"] for c in lang_context[:limit]]


class TranslationManager:
    """Главный класс для управления переводами."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.preferences = TranslationPreferences()
        self.history = TranslationHistory(self.preferences)
        self.context = ContextMemory(self.preferences)
        self.api_key = api_key
        
        # Инициализация Gemini API
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key, http_options={"api_version": "v1beta"})
        else:
            self.client = None
    
    def translate(self, text: str, target_lang: Optional[str] = None, 
                  source_lang: Optional[str] = None) -> str:
        """
        Переводит текст на целевой язык.
        
        Args:
            text: Текст для перевода
            target_lang: Целевой язык (код языка)
            source_lang: Исходный язык (код языка)
            
        Returns:
            Переведённый текст
        """
        if not self.client:
            return "Извините, API ключ не настроен для перевода."
        
        # Определяем целевой язык
        if not target_lang:
            target_lang = self.preferences.get_default_target_language()
        
        # Определяем исходный язык
        if not source_lang:
            source_lang = self.preferences.preferences.get("source_language", "ru")
        
        # Получаем контекст для лучшего перевода
        context_memory = self.context.get_recent_context(target_lang)
        context_str = "\n".join(context_memory) if context_memory else ""
        
        # Формируем prompt для перевода
        prompt = self._build_translation_prompt(text, source_lang, target_lang, context_str)
        
        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            translation = response.text.strip()
            
            # Сохраняем в историю
            self.history.add_translation(text, translation, source_lang, target_lang)
            
            # Добавляем в контекстную память
            self.context.add_context(text, translation, target_lang)
            
            return translation
        except Exception as e:
            return f"Ошибка перевода: {str(e)}"
    
    def _build_translation_prompt(self, text: str, source_lang: str, 
                                target_lang: str, context: str) -> str:
        """
        Строит prompt для перевода.
        
        Args:
            text: Текст для перевода
            source_lang: Исходный язык
            target_lang: Целевой язык
            context: Контекстная память
            
        Returns:
            Prompt для Gemini
        """
        lang_names = {
            "ru": "русский",
            "en": "английский",
            "fr": "французский",
            "de": "немецкий",
            "es": "испанский",
            "zh": "китайский",
            "ja": "японский",
            "it": "итальянский",
            "pt": "португальский",
            "uz": "узбекский"
        }
        
        source_name = lang_names.get(source_lang, source_lang)
        target_name = lang_names.get(target_lang, target_lang)
        
        prompt = f"""Переведи следующий текст с {source_name} на {target_name}:

Текст: {text}

"""
        
        if context:
            prompt += f"Контекст предыдущих переводов на {target_name}:\n{context}\n"
        
        prompt += "Верни только перевод, без дополнительных объяснений."
        
        return prompt
    
    def get_language_name(self, lang_code: str) -> str:
        """Возвращает название языка по коду."""
        lang_info = self.preferences.get_language_info(lang_code)
        if lang_info:
            return lang_info["name"]
        return lang_code
    
    def get_available_languages(self) -> List[Dict[str, Any]]:
        """Возвращает список доступных языков."""
        return self.preferences.preferences.get("target_languages", [])
    
    def enable_language(self, lang_code: str) -> bool:
        """Включает язык."""
        return self.preferences.enable_language(lang_code)
    
    def disable_language(self, lang_code: str) -> bool:
        """Отключает язык."""
        return self.preferences.disable_language(lang_code)
    
    def set_default_language(self, lang_code: str) -> bool:
        """Устанавливает язык по умолчанию."""
        return self.preferences.set_default_target_language(lang_code)
    
    def search_translations(self, query: str) -> List[Dict[str, Any]]:
        """Ищет переводы по запросу."""
        return self.history.search_history(query)
    
    def get_recent_translations(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Возвращает последние переводы."""
        return self.history.get_recent_translations(limit)
    
    def toggle_learning_mode(self, enabled: bool) -> bool:
        """Включает/выключает режим изучения."""
        return self.preferences.toggle_learning_mode(enabled)
    
    def is_learning_mode_enabled(self) -> bool:
        """Проверяет включён ли режим изучения."""
        return self.preferences.preferences.get("learning_mode", {}).get("enabled", False)
    
    def get_learning_target(self) -> str:
        """Возвращает целевой язык для изучения."""
        return self.preferences.preferences.get("learning_mode", {}).get("target_language", "en")


# ─── Удобные функции ───────────────────────────────────────────────────────────────
def translate_text(text: str, target_lang: str = "english") -> str:
    """
    Переводит текст.
    
    Args:
        text: Текст для перевода
        target_lang: Целевой язык
        
    Returns:
        Переведённый текст
    """
    import json
    from pathlib import Path
    
    _BASE = Path(__file__).parent.parent
    api_key_file = _BASE / "config" / "api_keys.json"
    
    try:
        with open(api_key_file, "r", encoding="utf-8") as f:
            api_key = json.load(f).get("gemini_api_key", "")
    except Exception:
        api_key = ""
    
    manager = TranslationManager(api_key)
    return manager.translate(text, target_lang)


def get_translation_history(date_range: str = "all") -> List[Dict[str, Any]]:
    """
    Возвращает историю переводов.
    
    Args:
        date_range: Период (today/all)
        
    Returns:
        Список переводов
    """
    import json
    from pathlib import Path
    
    _BASE = Path(__file__).parent.parent
    api_key_file = _BASE / "config" / "api_keys.json"
    
    try:
        with open(api_key_file, "r", encoding="utf-8") as f:
            api_key = json.load(f).get("gemini_api_key", "")
    except Exception:
        api_key = ""
    
    manager = TranslationManager(api_key)
    
    if date_range == "today":
        return manager.history.get_today_translations()
    else:
        return manager.get_recent_translations(50)


def search_translations(query: str) -> List[Dict[str, Any]]:
    """
    Ищет переводы по запросу.

    Args:
        query: Поисковый запрос

    Returns:
        Список найденных переводов
    """
    import json
    from pathlib import Path

    _BASE = Path(__file__).parent.parent
    api_key_file = _BASE / "config" / "api_keys.json"

    try:
        with open(api_key_file, "r", encoding="utf-8") as f:
            api_key = json.load(f).get("gemini_api_key", "")
    except Exception:
        api_key = ""

    manager = TranslationManager(api_key)
    return manager.history.search_translations(query)


# ─── Настройки языков ──────────────────────────────────────────────────────────────
#
# Модели инструмент объявлен через НАЗВАНИЯ ("english", "french"), а файл настроек
# ключуется кодами ISO ("en", "fr"). Эти две системы координат никто не связывал,
# поэтому main.py правил JSON руками по несуществующим ключам: enable_language
# падало с KeyError, а set_default_language писало ключ, который никто не читает,
# и рапортовало об успехе. Разбор названия и все три операции живут здесь —
# рядом со схемой, которой они управляют.
_KNOWN_LANGUAGES = [
    # (код, английское имя, родное имя, русские синонимы)
    ("en", "English",    "English",    ("английский", "инглиш")),
    ("ru", "Russian",    "Русский",    ("русский",)),
    ("uz", "Uzbek",      "O'zbek",     ("узбекский",)),
    ("fr", "French",     "Français",   ("французский",)),
    ("de", "German",     "Deutsch",    ("немецкий",)),
    ("es", "Spanish",    "Español",    ("испанский",)),
    ("it", "Italian",    "Italiano",   ("итальянский",)),
    ("pt", "Portuguese", "Português",  ("португальский",)),
    ("zh", "Chinese",    "中文",        ("китайский",)),
    ("ja", "Japanese",   "日本語",      ("японский",)),
    ("ko", "Korean",     "한국어",      ("корейский",)),
    ("tr", "Turkish",    "Türkçe",     ("турецкий",)),
    ("ar", "Arabic",     "العربية",     ("арабский",)),
    ("kk", "Kazakh",     "Қазақша",    ("казахский",)),
]


def resolve_language_code(value: str) -> Optional[str]:
    """Название языка на любом из языков → код ISO. None, если не узнали.

    Принимает «english», «English», «английский», «en» и то, что пользователь
    сам дописал в target_languages. Сначала смотрим в настройки — там могут
    быть свои языки, — потом в общий список.
    """
    needle = (value or "").strip().lower()
    if not needle:
        return None

    prefs = TranslationPreferences().preferences.get("target_languages", [])
    for lang in prefs:
        variants = {
            str(lang.get("code", "")).lower(),
            str(lang.get("name", "")).lower(),
            str(lang.get("native_name", "")).lower(),
        }
        if needle in variants - {""}:
            return lang.get("code")

    for code, name, native, aliases in _KNOWN_LANGUAGES:
        if needle == code or needle == name.lower() or needle == native.lower():
            return code
        if needle in aliases:
            return code
    return None


def _language_entry(code: str) -> Dict[str, Any]:
    for c, name, native, _ in _KNOWN_LANGUAGES:
        if c == code:
            return {"code": c, "name": name, "native_name": native, "enabled": False}
    return {"code": code, "name": code, "native_name": code, "enabled": False}


def set_language_enabled(language: str, enabled: bool) -> Optional[str]:
    """Включает/выключает язык. Возвращает код или None, если язык неизвестен.

    Если язык известен, но в настройках его ещё нет (в объявлении инструмента
    обещан english, а в файле его не было) — добавляем, а не молчим.
    """
    code = resolve_language_code(language)
    if not code:
        return None
    prefs = TranslationPreferences()
    known = {lang.get("code") for lang in prefs.preferences.get("target_languages", [])}
    if code not in known:
        prefs.preferences.setdefault("target_languages", []).append(_language_entry(code))
    saved = prefs.enable_language(code) if enabled else prefs.disable_language(code)
    return code if saved else None


def set_default_language(language: str) -> Optional[str]:
    """Язык перевода по умолчанию. Возвращает код или None, если не узнали язык."""
    code = resolve_language_code(language)
    if not code:
        return None
    prefs = TranslationPreferences()
    return code if prefs.set_default_target_language(code) else None


def set_learning_mode(enabled: bool, language: Optional[str] = None) -> Optional[str]:
    """Режим изучения языка. Возвращает код языка ("" при выключении) или None."""
    prefs = TranslationPreferences()
    mode = prefs.preferences.setdefault("learning_mode", {})
    code = ""
    if enabled:
        code = resolve_language_code(language or "")
        if not code:
            return None
        mode["target_language"] = code
    mode["enabled"] = enabled
    return code if prefs.save_preferences() else None
