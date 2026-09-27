"""Ключи на ПК: config/api_keys.json в папке программы + %APPDATA%/JARVIS.

Окно «Ключи» и установленный JARVIS.exe хранят ключи в %APPDATA%/JARVIS
(core/paths.save_api_keys), а модули бота читали только config/ рядом с
кодом. В exe там пусто — и Fish Audio, запасная модель, отправка в
Telegram молча считали, что ключей нет (голос Джарвиса падал на
встроенный Gemini). Теперь — оба места, как load_api_keys: %APPDATA% главнее.
В облаке папки пользователя нет — остаётся config/ и переменные окружения.
"""
from __future__ import annotations

import json
from pathlib import Path


def read(config_file: Path) -> dict:
    raw: dict = {}
    try:
        raw = json.loads(Path(config_file).read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    try:
        import os
        import sys
        base = Path(os.getenv("APPDATA", str(Path.home()))) if sys.platform == "win32" else Path.home() / ".config"
        user = json.loads((base / "JARVIS" / "api_keys.json").read_text(encoding="utf-8"))
        if isinstance(user, dict):
            raw.update({k: v for k, v in user.items() if v not in ("", None)})
    except Exception:
        pass
    return raw
