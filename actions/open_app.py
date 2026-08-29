"""Действие: запуск и закрытие приложений на Windows / Linux.
"""

import logging
import os
import shutil
import subprocess
import sys

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


def open_app(parameters: dict, response=None, player=None) -> str:
    app_name = parameters.get("app_name", "").strip().lower()
    if not app_name:
        return "Не указано имя приложения."

    candidates = _ALIASES.get(app_name, [app_name])

    for cmd in candidates:
        if cmd.startswith("start "):
            # Команды URI протоколов (например start ms-settings:)
            try:
                os.system(cmd)
                return f"Открыл {app_name}."
            except Exception as e:
                _logger.warning("Ошибка запуска URI %s: %s", cmd, e)
                continue

        path = shutil.which(cmd)
        if path or sys.platform == "win32":
            try:
                # В Windows os.startfile или subprocess с DETACHED_PROCESS
                if sys.platform == "win32":
                    try:
                        os.startfile(cmd)
                        return f"Открыл {app_name}."
                    except Exception:
                        subprocess.Popen(
                            [path or cmd],
                            shell=True,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return f"Открыл {app_name}."
                else:
                    subprocess.Popen(
                        [path],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return f"Открыл {app_name}."
            except Exception as e:
                _logger.warning("Не удалось запустить %s: %s", cmd, e)

    return f"Не удалось найти приложение «{app_name}» в системе."
