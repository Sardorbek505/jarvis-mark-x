"""
Действие: открыть приложение
"""

import subprocess
import sys
import os
import shutil

import logging

_logger = logging.getLogger(__name__)


_ALIASES = {
    # Браузеры
    "chrome": ["google-chrome", "chrome", "chromium-browser", "chromium"],
    "хром": ["google-chrome", "chrome", "chromium-browser", "chromium"],
    "firefox": ["firefox"],
    "фаерфокс": ["firefox"],
    "edge": ["msedge", "microsoft-edge"],
    "браузер": ["xdg-open", "google-chrome", "firefox"],

    # Редакторы
    "vscode": ["code"],
    "vs code": ["code"],
    "визуал студио": ["code"],
    "sublime": ["subl"],
    "блокнот": ["gedit", "mousepad", "notepad"],

    # Терминал
    "терминал": ["gnome-terminal", "xterm", "konsole", "Terminal"],
    "terminal": ["gnome-terminal", "xterm", "konsole"],

    # Медиа
    "vlc": ["vlc"],
    "spotify": ["spotify"],
    "спотифай": ["spotify"],

    # Системные
    "менеджер файлов": ["nautilus", "thunar", "dolphin"],
    "настройки": ["gnome-control-center", "systemsettings"],
    "калькулятор": ["gnome-calculator", "kcalc", "galculator"],
    "почта": ["thunderbird", "evolution"],

    # Windows (если запускают под WSL или Windows)
    "notepad": ["notepad.exe"],
    "paint": ["mspaint.exe"],
    "explorer": ["explorer.exe"],
}


def open_app(parameters: dict, response=None, player=None) -> str:
    app_name = parameters.get("app_name", "").strip().lower()

    candidates = _ALIASES.get(app_name, [app_name])

    for cmd in candidates:
        if shutil.which(cmd):
            try:
                subprocess.Popen(
                    [cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                display = app_name.capitalize()
                if player:
                    player.write_log(f"SYS: Открыто — {display}")
                return f"Открываю {display}."
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    # Последняя попытка — через xdg-open или start
    try:
        if sys.platform == "win32":
            os.startfile(app_name)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", app_name])
        else:
            subprocess.Popen(["xdg-open", app_name],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Запускаю {app_name}."
    except Exception as e:
        return f"Не удалось открыть {app_name}: {e}"
