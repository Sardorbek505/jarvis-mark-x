"""
JARVIS — первый запуск: получение Gemini-ключа у пользователя (BYOK).

Модель BYOK (Bring Your Own Key): ключ Gemini принадлежит пользователю, счета
за API Google выставляет ему напрямую. Поэтому ключ нельзя зашивать в сборку —
его нужно спросить при первом запуске и сохранить в config/api_keys.json.

Порядок поиска ключа:
    1. переменная окружения GEMINI_API_KEY (удобно для CI и разработки);
    2. config/api_keys.json → "gemini_api_key";
    3. интерактивный мастер (если есть TTY).

Мастер проверяет ключ живым запросом к ListModels перед сохранением, чтобы
пользователь узнал об опечатке сразу, а не через минуту падения аудио-сессии.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from core.storage import atomic_write_json, safe_read_json

_logger = logging.getLogger(__name__)

_ENV_VAR = "GEMINI_API_KEY"
_KEY_FIELD = "gemini_api_key"
_VALIDATE_URL = "https://generativelanguage.googleapis.com/v1beta/models?key={key}"
_VALIDATE_TIMEOUT = 15
_KEY_MIN_LEN = 30
_MAX_ATTEMPTS = 3
_APIKEY_PAGE = "https://aistudio.google.com/apikey"


def _mask(key: str) -> str:
    """Ключ в логах и на экране — только хвост, чтобы не светить целиком."""
    return f"…{key[-4:]}" if len(key) > 4 else "…"


def validate_key(key: str) -> tuple[bool, str]:
    """Проверяет ключ живым запросом. Возвращает (валиден, причина)."""
    if len(key) < _KEY_MIN_LEN:
        return False, "ключ слишком короткий — похоже, скопирован не полностью"
    try:
        with urllib.request.urlopen(
            _VALIDATE_URL.format(key=key), timeout=_VALIDATE_TIMEOUT
        ) as resp:
            json.loads(resp.read().decode())
        return True, ""
    except urllib.error.HTTPError as e:
        if e.code in (400, 403):
            return False, "Google отклонил ключ (недействителен или без доступа к Gemini API)"
        return False, f"Google ответил ошибкой {e.code}"
    except urllib.error.URLError as e:
        return False, f"нет связи с Google ({e.reason})"
    except (json.JSONDecodeError, OSError) as e:
        return False, f"неожиданный ответ: {type(e).__name__}"


def save_key(config_path: Path, key: str) -> None:
    """Кладёт ключ в config/api_keys.json, не затирая остальные настройки."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = safe_read_json(config_path, default={})
    if not isinstance(cfg, dict):
        cfg = {}
    atomic_write_json(config_path, {**cfg, _KEY_FIELD: key})


def _prompt_for_key(config_path: Path) -> str | None:
    """Интерактивный мастер. Возвращает валидный ключ либо None."""
    print()
    print("=" * 64)
    print("  ДЖАРВИС - первый запуск")
    print("=" * 64)
    print()
    print("  Джарвису нужен ваш собственный ключ Google Gemini.")
    print("  Ключ остаётся на этом компьютере и используется только отсюда.")
    print()
    print(f"  1. Откройте {_APIKEY_PAGE}")
    print("  2. Войдите в аккаунт Google и нажмите «Create API key»")
    print("  3. Скопируйте ключ и вставьте его ниже")
    print()

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            key = input("  Ключ Gemini: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Настройка прервана.")
            return None

        if not key:
            print("  Пусто. Попробуйте ещё раз.\n")
            continue

        print("  Проверяю ключ...")
        ok, reason = validate_key(key)
        if ok:
            save_key(config_path, key)
            print(f"  [OK] Ключ принят и сохранён в {config_path}")
            print()
            return key

        print(f"  [!] Не подошёл: {reason}")
        if attempt < _MAX_ATTEMPTS:
            print("  Попробуйте ещё раз.\n")

    print(f"\n  Не удалось настроить ключ за {_MAX_ATTEMPTS} попытки.")
    print(f"  Получить ключ: {_APIKEY_PAGE}")
    return None


def ensure_gemini_key(config_path: Path | None = None, *, interactive: bool = True) -> str | None:
    """Возвращает ключ Gemini или None, если получить его не удалось."""
    env_key = os.environ.get(_ENV_VAR, "").strip()
    if env_key:
        _logger.info("Ключ Gemini взят из %s (%s)", _ENV_VAR, _mask(env_key))
        return env_key

    if config_path is not None:
        cfg = safe_read_json(config_path, default={})
        stored = (cfg or {}).get(_KEY_FIELD, "").strip() if isinstance(cfg, dict) else ""
        if stored:
            return stored
    else:
        try:
            from core.paths import load_api_keys
            stored = load_api_keys().get(_KEY_FIELD, "").strip()
            if stored:
                return stored
        except Exception as _e:
            _logger.debug("load_api_keys lookup failed: %s", _e)

    if not interactive or not sys.stdin.isatty():
        _logger.error("Ключ Gemini не настроен, а мастер запустить негде (нет TTY)")
        return None

    try:
        from core.paths import get_config_path
        target_path = config_path if config_path is not None else get_config_path("api_keys.json", for_writing=True)
    except Exception:
        target_path = config_path or Path("config/api_keys.json")
    return _prompt_for_key(target_path)
