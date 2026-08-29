"""JARVIS Mark X — Единая система управления путями и конфигурацией.

Корректно работает как в режиме разработки (исходный код), так и в собранном
бинарнике PyInstaller (frozen .exe с _internal и _MEIPASS), а также гарантирует
запись пользовательских настроек в %APPDATA%/JARVIS без требования прав Администратора.
"""
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("jarvis-paths")


def get_base_dir() -> Path:
    """Возвращает базовую директорию ресурсов приложения (dev или _MEIPASS в .exe)."""
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).parent / "_internal"
    return Path(__file__).resolve().parent.parent


def get_app_dir() -> Path:
    """Директория с исполняемым файлом (куда установлен .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def get_user_data_dir() -> Path:
    """Возвращает гарантированно доступную для записи папку пользователя."""
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA", str(Path.home())))
    else:
        base = Path.home() / ".config"
    user_dir = base / "JARVIS"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_config_path(filename: str = "api_keys.json", for_writing: bool = False) -> Path:
    """Возвращает путь к файлу конфигурации.

    Если for_writing=True — возвращает путь в папке пользователя (%APPDATA%/JARVIS).
    Если for_writing=False — проверяет сначала %APPDATA%, затем встроенный каталог config/.
    """
    user_path = get_user_data_dir() / filename
    if for_writing:
        return user_path

    # Чтение: сначала проверяем пользовательские настройки
    if user_path.exists():
        return user_path

    # Затем проверяем встроенный конфиг в корне или _internal
    base_config = get_base_dir() / "config" / filename
    if base_config.exists():
        return base_config

    app_config = get_app_dir() / "config" / filename
    if app_config.exists():
        return app_config

    return user_path


def get_prompt_path() -> Path:
    """Возвращает путь к системному промпту Джарвиса (core/prompt.txt)."""
    # 1. В ресурсах сборки / репозитория
    p = get_base_dir() / "core" / "prompt.txt"
    if p.exists():
        return p
    # 2. Рядом с исполняемым файлом
    p2 = get_app_dir() / "core" / "prompt.txt"
    if p2.exists():
        return p2
    return p


def load_api_keys() -> dict:
    """Загружает API ключи и настройки из всех возможных источников."""
    # 1. Сначала из %APPDATA%
    user_cfg = get_user_data_dir() / "api_keys.json"
    data = {}
    if user_cfg.exists():
        try:
            data.update(json.loads(user_cfg.read_text(encoding="utf-8")))
        except Exception:
            pass

    # 2. Если в APPDATA чего-то нет, подмешиваем из локального config/api_keys.json
    local_cfg = get_app_dir() / "config" / "api_keys.json"
    if local_cfg.exists():
        try:
            local_data = json.loads(local_cfg.read_text(encoding="utf-8"))
            for k, v in local_data.items():
                if k not in data or not data[k]:
                    data[k] = v
        except Exception:
            pass

    return data


def save_api_keys(data: dict) -> bool:
    """Сохраняет API ключи в пользовательскую директорию %APPDATA% и локально."""
    saved = False
    for target in [get_user_data_dir() / "api_keys.json", get_app_dir() / "config" / "api_keys.json"]:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if target.exists():
                try:
                    existing = json.loads(target.read_text(encoding="utf-8"))
                except Exception:
                    pass
            existing.update(data)
            target.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
            saved = True
        except Exception as e:
            logger.debug("Failed to write to %s: %s", target, e)
    return saved
