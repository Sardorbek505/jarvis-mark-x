"""Действие: открыть программу по имени («открой телеграм», «запусти стим»).

Порядок: уже запущена — выводим её окно вперёд (как Siri: не плодим копии);
встроенное в Windows — запускаем сразу; иначе ищем в индексе программ
(меню «Пуск», Microsoft Store, ярлыки, известные пути — core/win_apps.py).
Отвечаем тем, что реально произошло, а не «Открыл» наугад.
"""

import logging
import os
import shutil
import subprocess
import sys

from core import win_apps

_logger = logging.getLogger(__name__)


def open_app(parameters: dict, response=None, player=None) -> str:
    raw = (parameters.get("app_name") or "").strip()
    if not raw:
        return "Не указано имя приложения."
    if any(c in raw for c in ("\\", "/")) or (len(raw) > 2 and raw[1] == ":"):
        # Путь к файлу — не приложение. Раньше «открой c:\...\x.bat» или
        # \\сервер\share\x.exe (из Telegram или от модели) запускались как есть.
        return f"Запускаю только приложения по имени, а не файлы по пути: «{raw}»."

    key = win_apps.canonical(raw)

    if key in ("браузер", "browser"):
        import webbrowser
        webbrowser.open("https://www.google.com")
        return "Открыл браузер."

    if sys.platform != "win32":
        return _open_posix(key, raw)

    # 1. Уже запущена — просто показать.
    running = win_apps.find_windows(key)
    if running and key not in ("explorer",):
        win = running[0]
        if win_apps.focus(win):
            return f"{_title(raw)} уже открыт — переключил на него."

    # 2. Встроенное в Windows.
    for cmd in win_apps.BUILTIN.get(key, []):
        try:
            os.startfile(cmd)  # type: ignore[attr-defined]
            return f"Открыл {_title(raw)}."
        except OSError:
            continue

    # 3. Индекс программ.
    app = win_apps.find_app(key)
    if app is None:
        app = win_apps.find_app(key, win_apps.build_index(force=True))   # поставили только что
    if app:
        try:
            win_apps.launch(app)
            if player:
                player.write_log(f"SYS: запускаю {app.name}")
            return f"Открыл {app.name}."
        except Exception as exc:
            _logger.warning("Запуск %s: %s", app, exc)
            return f"Нашёл {app.name}, но не смог запустить: {exc}"

    # 4. Последний шанс: имя exe в PATH / App Paths.
    for cmd in (f"{key}.exe", key):
        path = shutil.which(cmd)
        if path:
            os.startfile(path)  # type: ignore[attr-defined]
            return f"Открыл {_title(raw)}."

    near = win_apps.suggestions(raw)
    hint = f" Похожие: {', '.join(near)}." if near else ""
    return f"Не нашёл программу «{raw}» на компьютере.{hint}"


def _title(raw: str) -> str:
    return raw[:1].upper() + raw[1:]


def _open_posix(key: str, raw: str) -> str:
    for cmd in (key, raw.lower()):
        path = shutil.which(cmd)
        if path:
            subprocess.Popen([path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return f"Открыл {_title(raw)}."
    return f"Не нашёл программу «{raw}»."
