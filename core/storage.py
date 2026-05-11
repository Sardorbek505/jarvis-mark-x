"""
Безопасные операции с JSON-файлами.

Атомарная запись: пишем в .tmp → os.replace().
Recoverable loss: при corrupt JSON делаем .broken-<ts> backup,
не теряем данные молча.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """
    Атомарно записывает JSON. При сбое посередине — старый файл цел.

    Пишет в `path.tmp`, делает `os.replace()` (атомарно на одной FS).
    Создаёт родительские директории если их нет.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # Не оставляем мусор
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def safe_read_json(path: Path, default: Any = None) -> Any:
    """
    Читает JSON. При corrupt файле:
      1. Делает backup в `<path>.broken-<ts>`
      2. Логирует через print (logging framework пока нет)
      3. Возвращает `default`

    НЕ возвращает молча default при ошибке — это маскировка data loss.
    """
    path = Path(path)
    if not path.exists():
        return default if default is not None else {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        # Бэкап повреждённого файла
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(path.suffix + f".broken-{ts}")
        try:
            path.replace(backup)
            print(f"[storage] ⚠️ Повреждён {path}: {e}. Backup → {backup.name}")
        except Exception as backup_err:
            print(f"[storage] ⚠️ Повреждён {path}: {e}. Не удалось сделать backup: {backup_err}")
        return default if default is not None else {}
    except OSError as e:
        print(f"[storage] ⚠️ Ошибка чтения {path}: {e}")
        return default if default is not None else {}
