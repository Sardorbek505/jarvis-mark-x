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
from datetime import datetime
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


def _размер(байт: int) -> str:
    """Размер словами, которые человек слышит: «1,4 ГБ», а не «1503238553»."""
    for предел, имя in ((1024 ** 3, "ГБ"), (1024 ** 2, "МБ"), (1024, "КБ")):
        if байт >= предел:
            return f"{байт / предел:.1f} {имя}".replace(".", ",")
    return f"{байт} Б"


def _когда(отметка: float) -> str:
    return datetime.fromtimestamp(отметка).strftime("%d.%m.%Y %H:%M")


# Сколько файлов обойти, прежде чем признать обход слишком долгим. Домашняя
# папка легко содержит сотни тысяч файлов: без предела «какие файлы самые
# большие» превращается в минуту тишины, а голосовой помощник за минуту
# молчания успевает показаться сломанным.
_ПРЕДЕЛ_ОБХОДА = 50_000


def _обойти(корень: Path) -> tuple[list[Path], bool]:
    """Файлы под корнем и признак того, что обход был прерван по пределу."""
    найдено: list[Path] = []
    for путь in корень.rglob("*"):
        try:
            if путь.is_file():
                найдено.append(путь)
        except OSError:
            continue           # битая ссылка или отозванный доступ
        if len(найдено) >= _ПРЕДЕЛ_ОБХОДА:
            return найдено, True
    return найдено, False


# Куда что раскладывать. Расширения, которых здесь нет, остаются на месте:
# «Прочее» — это папка, в которую человек складывает то, чего не понял сам,
# а не то, чего не поняли мы. Ярлыки не трогаются вовсе: перенесённый ярлык
# ломает привычку открывать программу с рабочего стола.
_РАСКЛАДКА = {
    "Картинки":  {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tiff"},
    "Документы": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx",
                  ".ods", ".ppt", ".pptx", ".csv", ".md", ".epub"},
    "Музыка":    {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma"},
    "Видео":     {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v"},
    "Архивы":    {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Программы": {".exe", ".msi", ".dmg", ".deb", ".rpm", ".appimage", ".apk"},
}

_ПО_РАСШИРЕНИЮ = {
    расш: папка for папка, расширения in _РАСКЛАДКА.items() for расш in расширения
}


def _разложить(корень: Path, player=None) -> str:
    """Раскладывает файлы папки по типам. Одной отменой возвращается всё."""
    if not корень.is_dir():
        return f"Папка не найдена: {корень}"

    переезды: list[tuple[Path, Path]] = []
    созданы: list[Path] = []
    оставлены = 0
    занято = 0

    for файл in sorted(корень.iterdir()):
        if not файл.is_file():
            continue
        папка = _ПО_РАСШИРЕНИЮ.get(файл.suffix.lower())
        if папка is None:
            оставлены += 1
            continue

        назначение = корень / папка
        if not назначение.exists():
            назначение.mkdir()
            созданы.append(назначение)
        цель = назначение / файл.name
        if цель.exists():
            # Перезаписать — значит потерять чужой файл ради порядка.
            занято += 1
            continue
        shutil.move(str(файл), str(цель))
        переезды.append((файл, цель))

    if not переезды:
        for папка in созданы:
            папка.rmdir()
        return f"В {корень.name} раскладывать нечего, сэр."

    def _вернуть(список=list(переезды), папки=list(созданы)):
        возвращено = 0
        for откуда, куда in список:
            try:
                shutil.move(str(куда), str(откуда))
                возвращено += 1
            except OSError as exc:
                _logger.warning("Файл %s не вернулся: %s", куда, exc)
        for папка in папки:
            try:
                папка.rmdir()
            except OSError:
                pass          # человек успел что-то туда положить — пусть стоит
        return f"Вернул на место {возвращено} файл(ов)."

    push_undo(f"раскладка папки {корень.name}", _вернуть)

    по_папкам: dict[str, int] = {}
    for _, куда in переезды:
        по_папкам[куда.parent.name] = по_папкам.get(куда.parent.name, 0) + 1
    разбивка = ", ".join(f"{имя} — {сколько}" for имя, сколько in sorted(по_папкам.items()))

    if player:
        player.write_log(f"FILE: раскладка {корень.name}, перемещено {len(переезды)}")

    хвост = ""
    if оставлены:
        хвост += f" Незнакомых файлов оставил на месте: {оставлены}."
    if занято:
        хвост += f" Столько же имён уже было занято: {занято} — эти не трогал."
    return (f"Разложил {len(переезды)} файл(ов) в {корень.name}: {разбивка}.{хвост} "
            f"Скажите «отмени», и всё вернётся на места.")


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
    extension = str(parameters.get("extension", "")).strip().lstrip("*").lower()
    # Путь по умолчанию для раскладки — рабочий стол, а не домашняя папка:
    # «разбери у меня тут» про домашнюю папку не говорят, а разложить её
    # целиком — это переезд, которого никто не просил.
    if action in ("organize", "organize_desktop") and not parameters.get("path"):
        path = _SHORTCUTS["desktop"]

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

        elif action == "append":
            p = Path(path)
            existed = p.is_file()
            было = p.stat().st_size if existed else 0

            # Перевод строки перед дописанным, если его там нет. Иначе вторая
            # мысль приклеивается к концу первой одним словом.
            разделитель = ""
            if было:
                with p.open("rb") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) not in (b"\n", b"\r"):
                        разделитель = "\n"

            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(разделитель + content)

            if player:
                player.write_log(f"FILE: Дописано в {p.name}")

            if existed:
                # Отмена обрезкой до прежней длины, а не возвратом снимка:
                # точно, мгновенно и работает с файлом любого размера — тогда
                # как снимок в памяти мы держать отказываемся уже с мегабайта.
                def _обрезать(target=p, длина=было):
                    with open(target, "r+b") as f:
                        f.truncate(длина)
                    return f"Дописанное в {target.name} убрано."
                push_undo(f"дописывание в {p.name}", _обрезать)
            else:
                def _unmake_appended(target=p):
                    target.unlink(missing_ok=True)
                    return f"{target.name} удалён."
                push_undo(f"создание файла {p.name}", _unmake_appended)
            return f"Дописал в {p.name} {len(content)} символ(ов)."

        elif action == "info":
            p = Path(path)
            if not p.exists():
                return f"Не найдено: {path}"
            изменён = _когда(p.stat().st_mtime)
            if p.is_dir():
                внутри = sum(1 for _ in p.iterdir())
                return (f"{p.name} — папка, внутри {внутри} элемент(ов), "
                        f"изменена {изменён}. Полный путь: {p}.")
            return (f"{p.name} — файл {p.suffix.lstrip('.').upper() or 'без расширения'}, "
                    f"{_размер(p.stat().st_size)}, изменён {изменён}. Полный путь: {p}.")

        elif action == "largest":
            корень = Path(path)
            if not корень.is_dir():
                return f"Папка не найдена: {path}"
            файлы, прервано = _обойти(корень)
            if not файлы:
                return f"В {корень.name} файлов нет."
            крупные = sorted(файлы, key=lambda f: f.stat().st_size, reverse=True)[:7]
            строки = [f"{f.name} — {_размер(f.stat().st_size)}" for f in крупные]
            if player:
                player.write_log(f"FILE: самые большие в {корень.name}")
            хвост = (f" Обход остановлен на {_ПРЕДЕЛ_ОБХОДА} файлах — "
                     f"возможно, где-то глубже есть и крупнее." if прервано else "")
            return f"Самое большое в {корень.name}: " + "; ".join(строки) + "." + хвост

        elif action in ("organize", "organize_desktop"):
            return _разложить(Path(path), player)

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
            if not base.is_dir():
                return f"Папка не найдена: {path}"

            # По расширению — отдельный случай, а не «имя, в котором есть
            # точка и три буквы»: «найди все pdf» через `*pdf*` нашло бы и
            # папку «pdf-скрипты», и файл «pdf_инструкция.txt».
            if extension:
                расш = extension if extension.startswith(".") else "." + extension
                образец = f"*{name}*{расш}" if name else f"*{расш}"
                чего = f"файлы {расш}" + (f" с «{name}» в имени" if name else "")
            elif name:
                образец = f"*{name}*"
                чего = f"файлы с именем «{name}»"
            else:
                return "Что искать, сэр? Назовите имя или расширение."

            results = [r for r in base.rglob(образец) if r.is_file()][:10]
            if not results:
                return f"{чего.capitalize()} не найдены в {base.name}."
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

# ─── Объявление для реестра действий ──────────────────────────────────────────
# Инструмент описывает себя сам: имя, текст для модели, схема аргументов и
# обработчик. core/action_loader.py находит это при запуске — ни списка в
# main.py, ни ветки в диспетчере для нового инструмента больше не нужно.
TOOL = {
    "name": "files",
    "description": (
        "Управляет файлами и папками: показывает список (list), читает (read), "
        "создаёт (create_file, create_folder), ДОПИСЫВАЕТ в конец файла "
        "(append — «добавь в список», «запиши ещё»), перемещает (move), "
        "копирует (copy), переименовывает (rename), удаляет (delete). "
        "Ищет по имени и по расширению (find: «найди все pdf» — extension=pdf). "
        "Рассказывает о файле: размер, дата, тип (info). "
        "Находит, что занимает место: самые большие файлы в папке (largest). "
        "Раскладывает файлы по папкам «Картинки», «Документы», «Музыка», "
        "«Видео», «Архивы», «Программы» (organize — «разбери рабочий стол», "
        "«наведи порядок в загрузках»; без пути берётся рабочий стол). "
        "Показывает свободное место (disk_usage). "
        "Чтобы ПЕРЕСКАЗАТЬ документ или ответить по его содержимому — "
        "file_processor, а не этот инструмент."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "list | read | info | create_file | append | create_folder | "
                    "delete | move | copy | rename | find | largest | organize | "
                    "disk_usage"
                )
            },
            "path":        {"type": "STRING", "description": "Путь к файлу/папке или: desktop, downloads, documents"},
            "destination": {"type": "STRING", "description": "Путь назначения для move/copy"},
            "content":     {"type": "STRING", "description": "Содержимое для create_file и append"},
            "new_name":    {"type": "STRING", "description": "Новое имя для rename"},
            "name":        {"type": "STRING", "description": "Имя или его часть для поиска (find)"},
            "extension":   {"type": "STRING", "description": "Расширение для поиска: pdf, docx, mp3 (find)"},
        },
        "required": ["action"]
    },
    "handler": file_controller,
}
