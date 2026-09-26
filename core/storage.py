"""
Безопасные операции с JSON-файлами.

Атомарная запись: пишем в .tmp → os.replace().
Recoverable loss: при corrupt JSON делаем .broken-<ts> backup,
не теряем данные молча.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import logging

_logger = logging.getLogger(__name__)


_path_locks: dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _path_locks_guard:
        return _path_locks.setdefault(key, threading.Lock())


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """
    Атомарно записывает JSON. При сбое посередине — старый файл цел.

    Пишет во временный файл рядом и делает `os.replace()` (атомарно на одной
    FS). Временный файл — свой на каждую запись, а запись одного пути —
    под блокировкой: раньше два потока писали в общий `path.tmp`, и на
    Windows один из них падал на os.replace (WinError 32) — сохранение
    терялось. Создаёт родительские директории если их нет.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_for(path):
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            # Не оставляем мусор
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception as exc:
                _logger.warning("Подавлено исключение: %s", exc, exc_info=True)
            raise


def safe_read_json(path: Path, default: Any = None) -> Any:
    """
    Читает JSON. При corrupt файле:
      1. Делает backup в `<path>.broken-<ts>`
      2. Логирует ошибку
      3. Возвращает `default`

    НЕ возвращает молча default при ошибке — это маскировка data loss.
    """
    path = Path(path)
    if not path.exists():
        return default if default is not None else {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # Бэкап повреждённого файла
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(path.suffix + f".broken-{ts}")
        try:
            path.replace(backup)
            _logger.error("Повреждён %s: %s. Backup: %s", path, e, backup.name)
        except OSError as backup_err:
            _logger.error(
                "Повреждён %s: %s. Не удалось сделать backup: %s", path, e, backup_err
            )
        return default if default is not None else {}
    except OSError as e:
        _logger.error("Ошибка чтения %s: %s", path, e)
        return default if default is not None else {}


def load_json_or_quarantine(f) -> Any:
    """json.load(f), но сломанный файл сначала откладывается в `.broken-<ts>`.

    Для модулей вида «try: json.load(f) except: return умолчания». Раньше
    они молча брали умолчания, а следующее же сохранение затирало файл —
    календарь или история пропадали целиком из-за одной битой строки или
    сохранения в Блокноте в cp1251. Теперь оригинал остаётся рядом.
    """
    try:
        return json.loads(f.read())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        path = Path(f.name)
        f.close()                       # Windows не переименует открытый файл
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(path.suffix + f".broken-{ts}")
        try:
            path.replace(backup)
            _logger.error("Повреждён %s: %s. Копия: %s", path, e, backup.name)
        except OSError as backup_err:
            _logger.error("Повреждён %s: %s. Копию сделать не удалось: %s", path, e, backup_err)
        raise
