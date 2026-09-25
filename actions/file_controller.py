"""
Действие: работа с файлами и папками
"""

import os
import shutil
from pathlib import Path


_SHORTCUTS = {
    "desktop": os.path.join(os.path.expanduser("~"), "Desktop"),
    "рабочий стол": os.path.join(os.path.expanduser("~"), "Desktop"),
    "downloads": os.path.join(os.path.expanduser("~"), "Downloads"),
    "загрузки": os.path.join(os.path.expanduser("~"), "Downloads"),
    "documents": os.path.join(os.path.expanduser("~"), "Documents"),
    "документы": os.path.join(os.path.expanduser("~"), "Documents"),
    "home": os.path.expanduser("~"),
    "домашняя": os.path.expanduser("~"),
}


def _resolve(path: str) -> str:
    return _SHORTCUTS.get(path.lower().strip(), path)


def _is_protected(p: Path) -> bool:
    """Папки, которые голосом не удаляются никогда: дом, рабочий стол,
    загрузки, документы, их родители и корни дисков."""
    try:
        target = p.expanduser().resolve()
    except OSError:
        return True
    home = Path(os.path.expanduser("~")).resolve()
    protected = {home, *(Path(v).resolve() for v in _SHORTCUTS.values())}
    if target in protected or target == Path(target.anchor):
        return True
    return target in home.parents


def _to_recycle_bin(p: Path) -> None:
    """Windows: в Корзину (можно вернуть). Иначе — обычное удаление."""
    import sys
    if sys.platform != "win32":
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT = 3, 0x40, 0x10, 0x4
    op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=str(p.resolve()) + "\0\0",
                         fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError(f"не удалось переместить в Корзину (код {rc})")


def file_controller(parameters: dict, player=None) -> str:
    action = parameters.get("action", "list").lower()
    raw_path = str(parameters.get("path") or "").strip()
    path = _resolve(raw_path or os.path.expanduser("~"))
    dest = _resolve(parameters.get("destination", ""))
    name = parameters.get("name", "")
    content = parameters.get("content", "")
    new_name = parameters.get("new_name", "")

    try:
        if action == "list":
            p = Path(path)
            if not p.exists():
                return f"Папка не найдена: {path}"
            items = list(p.iterdir())
            dirs = [f"📁 {i.name}" for i in items if i.is_dir()]
            files = [f"📄 {i.name}" for i in items if i.is_file()]
            all_items = dirs + files
            if not all_items:
                return f"Папка пуста: {path}"
            preview = all_items[:12]
            extra = f" (+{len(all_items)-12} ещё)" if len(all_items) > 12 else ""
            if player:
                player.write_log(f"SYS: Файлы в {path}")
            return f"Содержимое {path}: {', '.join(preview)}{extra}."

        elif action == "read":
            p = Path(path)
            if not p.exists():
                return f"Файл не найден: {path}"
            text = p.read_text(encoding="utf-8", errors="replace")
            preview = text[:600]
            if player:
                player.write_log(f"FILE: Читаю {p.name}")
            return f"Содержимое {p.name}: {preview}{'...' if len(text) > 600 else ''}"

        elif action == "create_file":
            p = Path(path)
            if p.exists():
                return f"Файл уже существует, не перезаписываю: {p}."
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            if player:
                player.write_log(f"FILE: Создан {p.name}")
            return f"Файл создан: {p}."

        elif action == "create_folder":
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            if player:
                player.write_log(f"FILE: Папка создана {p.name}")
            return f"Папка создана: {p}."

        elif action == "delete":
            # Раньше путь по умолчанию был домашней папкой, а «desktop» —
            # всем рабочим столом: вызов без пути делал rmtree(~).
            if not raw_path:
                return "Не удаляю: не указано, что именно удалить."
            p = Path(path)
            if _is_protected(p):
                return f"Не удаляю системную папку целиком: {p}."
            if not p.exists():
                return f"Не найдено: {path}"
            _to_recycle_bin(p)
            if player:
                player.write_log(f"FILE: В корзину {p.name}")
            return f"Удалено (в Корзину): {p.name}."

        elif action == "move":
            shutil.move(path, dest)
            if player:
                player.write_log(f"FILE: Перемещено → {dest}")
            return f"Перемещено в {dest}."

        elif action == "copy":
            src = Path(path)
            if src.is_dir():
                shutil.copytree(path, dest)
            else:
                shutil.copy2(path, dest)
            if player:
                player.write_log(f"FILE: Скопировано → {dest}")
            return f"Скопировано в {dest}."

        elif action == "rename":
            p = Path(path)
            new_p = p.parent / new_name
            p.rename(new_p)
            if player:
                player.write_log(f"FILE: Переименовано → {new_name}")
            return f"Переименовано в {new_name}."

        elif action == "find":
            base = Path(path)
            results = list(base.rglob(f"*{name}*"))[:10]
            if not results:
                return f"Файлы с именем «{name}» не найдены."
            found = [str(r) for r in results]
            return "Найдено: " + ", ".join(found[:5]) + ("..." if len(results) > 5 else ".")

        elif action == "disk_usage":
            # Используем домашнюю директорию — корректно на всех OS,
            # включая Windows (где "/" даёт неправильные данные).
            target = path if path and Path(path).exists() else os.path.expanduser("~")
            total, used, free = shutil.disk_usage(target)
            gb = 1024**3
            return (f"Диск: всего {total/gb:.1f} ГБ, "
                    f"использовано {used/gb:.1f} ГБ, "
                    f"свободно {free/gb:.1f} ГБ.")

        else:
            return f"Неизвестное действие: {action}"

    except Exception as e:
        return f"Ошибка файловой операции: {e}"
