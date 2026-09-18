"""
Действие: работа с файлами и папками

Каждая операция, меняющая диск, регистрирует в `core.undo`, как себя обратить:
перемещение — перемещением назад, переименование — переименованием назад,
создание — удалением, перезапись — возвратом прежнего содержимого. Голосовой
ассистент ослышивается, и «отмени» должно работать без участия рук.

Удаление — особый случай: восстановить файл из корзины программно нельзя, и
отмена, которая на самом деле не отменяет, хуже её отсутствия. Поэтому файлы
уходят в корзину ОС (send2trash), откуда их достаёт сам пользователь, а в
стек отмены такая операция не попадает.
"""

import logging
import os
import shutil
from pathlib import Path

from core.undo import push_undo, MAX_SNAPSHOT_BYTES

_logger = logging.getLogger(__name__)


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


def _to_trash(p: Path) -> bool:
    """Отправляет в корзину ОС. False — корзина недоступна, решает вызывающий."""
    try:
        from send2trash import send2trash
    except ImportError:
        return False
    try:
        send2trash(str(p))
        return True
    except Exception as exc:
        _logger.warning("Корзина отказала для %s: %s", p, exc)
        return False


def _snapshot(p: Path) -> str | None:
    """Прежнее содержимое файла для отмены перезаписи.

    None означает «откат невозможен»: файла не было, он слишком велик, чтобы
    держать его в памяти, или это не текст. Держать в сессии 200-мегабайтный
    лог ради отмены — не та цена, которую стоит платить молча."""
    try:
        if not p.is_file() or p.stat().st_size > MAX_SNAPSHOT_BYTES:
            return None
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _logger.debug("Снимок %s не снят: %s", p, exc)
        return None


def file_controller(parameters: dict, player=None) -> str:
    action = parameters.get("action", "list").lower()
    path = _resolve(parameters.get("path", os.path.expanduser("~")))
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
            existed = p.is_file()
            previous = _snapshot(p) if existed else None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            if player:
                player.write_log(f"FILE: Создан {p.name}")
            if existed:
                if previous is not None:
                    def _restore(target=p, text=previous):
                        target.write_text(text, encoding="utf-8")
                        return f"Прежнее содержимое {target.name} возвращено."
                    push_undo(f"перезапись файла {p.name}", _restore)
            else:
                def _unmake(target=p):
                    target.unlink(missing_ok=True)
                    return f"{target.name} удалён."
                push_undo(f"создание файла {p.name}", _unmake)
            return f"Файл создан: {p}."

        elif action == "create_folder":
            p = Path(path)
            existed = p.exists()
            p.mkdir(parents=True, exist_ok=True)
            if player:
                player.write_log(f"FILE: Папка создана {p.name}")
            if not existed:
                def _unmake_dir(target=p):
                    # Только пустую: обратное к «создай папку» — убрать папку,
                    # а не унести с собой всё, что в неё успели положить.
                    try:
                        target.rmdir()
                        return f"Папка {target.name} убрана."
                    except OSError:
                        return f"Папка {target.name} уже не пуста — оставил на месте."
                push_undo(f"создание папки {p.name}", _unmake_dir)
            return f"Папка создана: {p}."

        elif action == "delete":
            p = Path(path)
            if not p.exists():
                return f"Не найдено: {path}"
            if _to_trash(p):
                if player:
                    player.write_log(f"FILE: {p.name} → корзина")
                return f"{p.name} отправлен в корзину — оттуда можно вернуть."
            # Корзины нет. Удаляем по-настоящему и говорим об этом прямо:
            # молчание здесь означало бы, что человек считает файл
            # восстановимым, а его уже нет.
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            if player:
                player.write_log(f"FILE: Удалено безвозвратно {p.name}")
            return f"Удалено безвозвратно: {p.name}. Корзина на этой машине недоступна."

        elif action == "move":
            src = Path(path)
            before = str(src)
            shutil.move(path, dest)
            # Куда именно легло: при перемещении в папку имя сохраняется.
            landed = Path(dest) / src.name if Path(dest).is_dir() else Path(dest)
            if player:
                player.write_log(f"FILE: Перемещено → {dest}")

            def _move_back(now=landed, was=before):
                shutil.move(str(now), was)
                return f"{Path(was).name} вернулся на место."
            push_undo(f"перемещение {src.name} → {dest}", _move_back)
            return f"Перемещено в {dest}."

        elif action == "copy":
            src = Path(path)
            if src.is_dir():
                shutil.copytree(path, dest)
            else:
                shutil.copy2(path, dest)
            landed = Path(dest) / src.name if Path(dest).is_dir() else Path(dest)
            if player:
                player.write_log(f"FILE: Скопировано → {dest}")

            def _remove_copy(copy_path=landed):
                # Обратное к копированию — убрать копию, а не оригинал.
                if copy_path.is_dir():
                    shutil.rmtree(copy_path, ignore_errors=True)
                else:
                    copy_path.unlink(missing_ok=True)
                return f"Копия {copy_path.name} убрана."
            push_undo(f"копирование {src.name} → {dest}", _remove_copy)
            return f"Скопировано в {dest}."

        elif action == "rename":
            p = Path(path)
            new_p = p.parent / new_name
            old_name = p.name
            p.rename(new_p)
            if player:
                player.write_log(f"FILE: Переименовано → {new_name}")

            def _rename_back(now=new_p, was=p):
                now.rename(was)
                return f"Имя {was.name} возвращено."
            push_undo(f"переименование {old_name} → {new_name}", _rename_back)
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
