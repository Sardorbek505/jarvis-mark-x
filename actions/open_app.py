"""Действие: запуск и закрытие приложений на Windows / Linux.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)

_ALIASES = {
    # Браузеры
    "chrome": ["chrome.exe", "google-chrome", "chrome"],
    "хром": ["chrome.exe", "google-chrome", "chrome"],
    "firefox": ["firefox.exe", "firefox"],
    "фаерфокс": ["firefox.exe", "firefox"],
    "edge": ["msedge.exe", "msedge"],
    "браузер": ["msedge.exe", "chrome.exe", "firefox.exe", "explorer.exe"],
    "яндекс": ["browser.exe", "yandex.exe"],

    # Мессенджеры и связь
    "telegram": ["Telegram.exe", "telegram-desktop"],
    "телеграм": ["Telegram.exe", "telegram-desktop"],
    "телега": ["Telegram.exe", "telegram-desktop"],
    "discord": ["Discord.exe", "discord"],
    "дискорд": ["Discord.exe", "discord"],

    # Игры и игровые сервисы
    "steam": ["Steam.exe", "steam"],
    "стим": ["Steam.exe", "steam"],
    "counter-strike": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "counter strike": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "counter-strike 2": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "counter strike 2": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "cs": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "cs2": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "cs 2": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "cs:go": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "csgo": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "кс": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "кс2": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "кс 2": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "кс го": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "ксго": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "контра": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "контр страйк": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "контр-страйк": ["Counter-Strike 2.url", "Counter-Strike 2", "cs2.exe"],
    "dota": ["dota2.exe", "Dota 2.url", "Dota 2"],
    "dota 2": ["dota2.exe", "Dota 2.url", "Dota 2"],
    "дота": ["dota2.exe", "Dota 2.url", "Dota 2"],
    "дота 2": ["dota2.exe", "Dota 2.url", "Dota 2"],

    # Рабочий стол и популярные утилиты
    "obsidian": ["Obsidian.lnk", "Obsidian.exe", "obsidian"],
    "обсидиан": ["Obsidian.lnk", "Obsidian.exe", "obsidian"],
    "framer": ["Framer.lnk", "Framer.exe"],
    "фреймер": ["Framer.lnk", "Framer.exe"],
    "canva": ["Canva.lnk", "Canva.exe"],
    "канва": ["Canva.lnk", "Canva.exe"],
    "capcut": ["CapCut.exe", "CapCut.lnk"],
    "капкат": ["CapCut.exe", "CapCut.lnk"],
    "cisco packet tracer": ["Cisco Packet Tracer.lnk"],
    "packet tracer": ["Cisco Packet Tracer.lnk"],
    "пакет трейсер": ["Cisco Packet Tracer.lnk"],
    "mediaget": ["MediaGet.lnk", "mediaget.exe"],
    "медиагет": ["MediaGet.lnk", "mediaget.exe"],

    # Редакторы и IDE
    "vscode": ["code.cmd", "code.exe", "code"],
    "vs code": ["code.cmd", "code.exe", "code"],
    "визуал студио": ["code.cmd", "code.exe", "code"],
    "блокнот": ["notepad.exe", "notepad"],
    "notepad": ["notepad.exe", "notepad"],
    "sublime": ["subl.exe", "subl"],

    # Терминал
    "терминал": ["wt.exe", "cmd.exe", "powershell.exe", "gnome-terminal"],
    "terminal": ["wt.exe", "cmd.exe", "powershell.exe", "gnome-terminal"],
    "консоль": ["wt.exe", "cmd.exe", "powershell.exe"],
    "cmd": ["cmd.exe"],
    "powershell": ["powershell.exe"],

    # Медиа
    "spotify": ["Spotify.exe", "spotify"],
    "спотифай": ["Spotify.exe", "spotify"],
    "vlc": ["vlc.exe", "vlc"],

    # Системные приложения
    "калькулятор": ["calc.exe", "calc", "gnome-calculator"],
    "calc": ["calc.exe", "calc"],
    "paint": ["mspaint.exe", "mspaint"],
    "паинт": ["mspaint.exe", "mspaint"],
    "проводник": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "диспетчер задач": ["taskmgr.exe"],
    "taskmgr": ["taskmgr.exe"],
    "настройки": ["start ms-settings:", "gnome-control-center"],
}


def _clean_target_path(p: str) -> str:
    """Очищает путь/имя от внешних кавычек и раскрывает переменные окружения / тильду."""
    if not p:
        return ""
    cleaned = p.strip("\"' \t\r\n")
    try:
        cleaned = os.path.expandvars(os.path.expanduser(cleaned))
    except Exception:
        pass
    return cleaned


def _find_windows_shortcut(name: str) -> str | None:
    """Ищет ярлык (.lnk, .url) или исполняемый файл на Рабочем столе или в меню Пуск."""
    if sys.platform != "win32":
        return None

    clean_target = _clean_target_path(name)
    if not clean_target:
        return None

    if os.path.isabs(clean_target) and os.path.exists(clean_target):
        return clean_target

    search_dirs = [
        Path(os.path.expanduser("~/Desktop")),
        Path(os.environ.get("PUBLIC", "C:\\Users\\Public")) / "Desktop",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]

    target_lower = clean_target.lower()
    target_norm = re.sub(r"[^a-zа-я0-9]", "", target_lower)
    valid_exts = {".lnk", ".url", ".exe"}

    # 1. Проверяем точные совпадения имён файлов на Рабочем столе и в Пуске
    for sdir in search_dirs:
        if not sdir.exists():
            continue
        try:
            iterator = sdir.rglob("*") if "Start Menu" in str(sdir) else sdir.iterdir()
            for item in iterator:
                try:
                    if not item.is_file():
                        continue
                    if item.suffix.lower() not in valid_exts:
                        continue

                    stem_lower = item.stem.lower()
                    name_lower = item.name.lower()

                    if stem_lower == target_lower or name_lower == target_lower:
                        return str(item.resolve())

                    stem_norm = re.sub(r"[^a-zа-я0-9]", "", stem_lower)
                    if target_norm and stem_norm == target_norm:
                        return str(item.resolve())
                except Exception:
                    continue
        except Exception:
            continue

    # 2. Нестрогое частичное совпадение (если длина запроса >= 3 символов)
    if len(target_norm) >= 3:
        for sdir in search_dirs:
            if not sdir.exists():
                continue
            try:
                iterator = sdir.iterdir() if "Desktop" in str(sdir) else sdir.rglob("*")
                for item in iterator:
                    try:
                        if not item.is_file() or item.suffix.lower() not in valid_exts:
                            continue
                        stem_norm = re.sub(r"[^a-zа-я0-9]", "", item.stem.lower())
                        if target_norm in stem_norm:
                            return str(item.resolve())
                    except Exception:
                        continue
            except Exception:
                continue

    return None


def _resolve_windows_path(cmd: str) -> str | None:
    """Ищет исполняемый файл в стандартных папках установки Windows, если его нет в PATH."""
    if sys.platform != "win32":
        return None

    local_app = os.environ.get("LOCALAPPDATA", "")
    roaming_app = os.environ.get("APPDATA", "")
    prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
    prog_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")

    c_lower = cmd.lower()
    common_search_map = {
        "telegram.exe": [
            os.path.join(local_app, "Telegram Desktop", "Telegram.exe"),
        ],
        "discord.exe": [
            os.path.join(local_app, "Discord", "Update.exe"),
        ],
        "spotify.exe": [
            os.path.join(roaming_app, "Spotify", "Spotify.exe"),
        ],
        "chrome.exe": [
            os.path.join(prog_files, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(prog_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local_app, "Google", "Chrome", "Application", "chrome.exe"),
        ],
        "msedge.exe": [
            os.path.join(prog_files, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(prog_files_x86, "Microsoft", "Edge", "Application", "msedge.exe"),
        ],
        "code.exe": [
            os.path.join(local_app, "Programs", "Microsoft VS Code", "Code.exe"),
            os.path.join(prog_files, "Microsoft VS Code", "Code.exe"),
        ],
        "code.cmd": [
            os.path.join(local_app, "Programs", "Microsoft VS Code", "bin", "code.cmd"),
        ],
        "browser.exe": [
            os.path.join(local_app, "Yandex", "YandexBrowser", "Application", "browser.exe"),
        ],
        "obsidian.exe": [
            os.path.join(local_app, "Programs", "Obsidian", "Obsidian.exe"),
        ],
        "steam.exe": [
            os.path.join(prog_files_x86, "Steam", "steam.exe"),
            os.path.join(prog_files, "Steam", "steam.exe"),
        ],
    }

    if c_lower in common_search_map:
        for p in common_search_map[c_lower]:
            if os.path.exists(p):
                return p
    return None


def open_app(parameters: dict, response=None, player=None) -> str:
    raw_app_name = str(parameters.get("app_name", "")).strip()
    clean_app_name = _clean_target_path(raw_app_name)
    if not clean_app_name:
        return "Не указано имя приложения."

    # Если передан существующий путь к файлу/ярлыку — сразу запускаем его
    if os.path.exists(clean_app_name):
        try:
            if sys.platform == "win32":
                os.startfile(clean_app_name)
            else:
                subprocess.Popen(
                    [clean_app_name] if os.access(clean_app_name, os.X_OK) else ["xdg-open", clean_app_name],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            base_name = os.path.basename(clean_app_name)
            if player:
                player.write_log(f"APP: Запущено {base_name} ({clean_app_name})")
            return f"Открыл {base_name}."
        except Exception as e:
            _logger.warning("Ошибка запуска прямого пути %s: %s", clean_app_name, e)

    norm_key = clean_app_name.lower()
    candidates = _ALIASES.get(norm_key, [clean_app_name])
    if clean_app_name not in candidates:
        candidates = list(candidates) + [clean_app_name]

    for cmd in candidates:
        cmd_clean = _clean_target_path(cmd)
        if not cmd_clean:
            continue

        if cmd_clean.startswith("start "):
            # Команды URI протоколов (например start ms-settings:)
            uri = cmd_clean[len("start "):].strip()
            try:
                if sys.platform == "win32":
                    os.startfile(uri)
                else:
                    subprocess.Popen(["xdg-open", uri])
                if player:
                    player.write_log(f"APP: Открыт ресурс {uri}")
                return f"Открыл {clean_app_name}."
            except Exception as e:
                _logger.warning("Ошибка запуска URI %s: %s", cmd_clean, e)
                continue

        # Прямой путь в кандидатах
        if os.path.exists(cmd_clean):
            try:
                if sys.platform == "win32":
                    os.startfile(cmd_clean)
                else:
                    subprocess.Popen(
                        [cmd_clean] if os.access(cmd_clean, os.X_OK) else ["xdg-open", cmd_clean],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                if player:
                    player.write_log(f"APP: Запущено {clean_app_name} ({cmd_clean})")
                return f"Открыл {clean_app_name}."
            except Exception as e:
                _logger.warning("Ошибка запуска %s: %s", cmd_clean, e)
                continue

        # Проверяем наличие исполняемого файла в PATH, стандартных каталогах или ярлыках рабочего стола/меню Пуск
        path = shutil.which(cmd_clean) or _resolve_windows_path(cmd_clean) or _find_windows_shortcut(cmd_clean)
        if path and os.path.exists(path):
            try:
                if sys.platform == "win32":
                    os.startfile(path)
                else:
                    subprocess.Popen(
                        [path],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                if player:
                    player.write_log(f"APP: Запущено {clean_app_name} ({path})")
                return f"Открыл {clean_app_name}."
            except Exception as e:
                _logger.warning("Ошибка запуска %s: %s", path, e)
                continue

        # Встроенные системные утилиты Windows (notepad.exe, calc.exe, explorer.exe)
        if sys.platform == "win32" and cmd_clean.endswith(".exe"):
            try:
                os.startfile(cmd_clean)
                if player:
                    player.write_log(f"APP: Запущено {clean_app_name} ({cmd_clean})")
                return f"Открыл {clean_app_name}."
            except Exception:
                pass

    # Финальная попытка: динамический поиск ярлыка по исходному имени
    shortcut = _find_windows_shortcut(clean_app_name)
    if shortcut and os.path.exists(shortcut):
        try:
            if sys.platform == "win32":
                os.startfile(shortcut)
            else:
                subprocess.Popen(["xdg-open", shortcut])
            if player:
                player.write_log(f"APP: Запущено {clean_app_name} ({shortcut})")
            return f"Открыл {clean_app_name}."
        except Exception as e:
            _logger.warning("Ошибка запуска найденного ярлыка %s: %s", shortcut, e)

    return f"Не удалось найти приложение «{clean_app_name}» в системе."
