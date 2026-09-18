"""
Действие: управление компьютером — громкость, яркость, скриншот, и др.
"""

import logging
import os
import platform
import subprocess
import time

from core.undo import push_undo

_logger = logging.getLogger(__name__)

_OS = platform.system()

# Обратные пары для отмены. Только относительные изменения: «тише на 10» точно
# обращается «громче на 10», а «поставь 40%» — нет, потому что прежнего
# значения система не сообщает, и отмена восстанавливала бы догадку. Отмена,
# возвращающая выдуманное значение, хуже её отсутствия.
_INVERSE = {"up": "down", "down": "up", "mute": "mute"}


def computer_settings(parameters: dict, response=None, player=None) -> str:
    action = (parameters.get("action") or parameters.get("description") or "").lower()
    value = str(parameters.get("value") or "")

    # --- СКРИНШОТ ---
    if any(k in action for k in ["скриншот", "screenshot", "снимок экрана", "захват экрана"]):
        return _screenshot(player)

    # --- ГРОМКОСТЬ ---
    if any(k in action for k in ["громкость", "volume", "звук"]):
        if any(k in action for k in ["тише", "уменьш", "убав", "down", "decrease"]):
            return _volume("down", value, player)
        elif any(k in action for k in ["громче", "увелич", "up", "increase"]):
            return _volume("up", value, player)
        elif any(k in action for k in ["выкл", "mute", "без звука", "отключ"]):
            return _volume("mute", value, player)
        elif value.isdigit():
            return _volume("set", value, player)
        else:
            return _volume("up", "10", player)

    # --- ЯРКОСТЬ ---
    if any(k in action for k in ["яркость", "brightness"]):
        if any(k in action for k in ["уменьш", "down", "убав"]):
            return _brightness("down", value, player)
        elif any(k in action for k in ["увелич", "up", "добав"]):
            return _brightness("up", value, player)
        elif value.isdigit():
            return _brightness("set", value, player)
        else:
            return _brightness("up", "10", player)

    # --- БЛОКИРОВКА ---
    if any(k in action for k in ["заблок", "lock", "экран"]):
        return _lock(player)

    # --- ВЫКЛЮЧЕНИЕ ---
    if any(k in action for k in ["выключ", "shutdown", "перезагруз", "restart", "reboot"]):
        return _power(action, player)

    return f"Действие не распознано: {action}"


def _screenshot(player) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    home = os.path.expanduser("~")
    path = os.path.join(home, f"Desktop/screenshot_{ts}.png")
    try:
        if _OS == "Windows":
            # PowerShell screenshot
            ps_cmd = (
                f'Add-Type -AssemblyName System.Windows.Forms; '
                f'[System.Windows.Forms.Screen]::PrimaryScreen | Out-Null; '
                f'$bmp = New-Object System.Drawing.Bitmap([System.Windows.Forms.SystemInformation]::PrimaryMonitorSize.Width, [System.Windows.Forms.SystemInformation]::PrimaryMonitorSize.Height); '
                f'$g = [System.Drawing.Graphics]::FromImage($bmp); '
                f'$g.CopyFromScreen(0,0,0,0,$bmp.Size); '
                f'$bmp.Save("{path}")'
            )
            subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=10)
        elif _OS == "Darwin":
            subprocess.run(["screencapture", path], timeout=5)
        else:
            # Linux: попробуем несколько инструментов
            for tool in ["scrot", "gnome-screenshot", "import"]:
                result = subprocess.run([tool, path], capture_output=True, timeout=5)
                if result.returncode == 0:
                    break

        if player:
            player.write_log(f"FILE: Скриншот → {path}")
        return f"Скриншот сохранён: {path}"
    except Exception as e:
        return f"Ошибка скриншота: {e}"


def _register_inverse(kind: str, fn, mode: str, value: str) -> None:
    """Кладёт в стек отмены обратное действие — если оно вообще существует.

    `record=False` у обратного вызова обязателен: иначе отмена сама положила бы
    в стек свою отмену, и «отмени» начало бы качать громкость туда-сюда."""
    inverse = _INVERSE.get(mode)
    if not inverse:
        return
    # «Отмена» для mute держится на том, что это ПЕРЕКЛЮЧАТЕЛЬ: SendKeys на
    # Windows и `set-sink-mute toggle` на Linux. На macOS `set volume with
    # output muted` звук только выключает, так что повторный вызов ничего не
    # вернёт — молча выключить звук ещё раз хуже, чем не предлагать отмену.
    if mode == "mute" and _OS == "Darwin":
        return
    amount = value if value.isdigit() else "10"
    label = {
        "up":   f"{kind}: +{amount}",
        "down": f"{kind}: -{amount}",
        "mute": f"{kind}: переключение звука",
    }[mode]
    push_undo(label, lambda: fn(inverse, amount, None, record=False))


def _volume(mode: str, value: str, player, record: bool = True) -> str:
    try:
        if _OS == "Windows":
            if mode == "mute":
                _ps_volume("mute")
                msg = "Звук выключен."
            elif mode == "set" and value.isdigit():
                _ps_volume("set", int(value))
                msg = f"Громкость установлена: {value}%."
            elif mode == "up":
                v = int(value) if value.isdigit() else 10
                _ps_volume("up", v)
                msg = f"Громкость увеличена на {v}%."
            else:
                v = int(value) if value.isdigit() else 10
                _ps_volume("down", v)
                msg = f"Громкость уменьшена на {v}%."

        elif _OS == "Darwin":
            if mode == "mute":
                subprocess.run(["osascript", "-e", "set volume with output muted"])
                msg = "Звук выключен."
            elif mode == "set":
                v = max(0, min(100, int(value)))
                subprocess.run(["osascript", "-e", f"set volume output volume {v}"])
                msg = f"Громкость: {v}%."
            else:
                subprocess.run(["osascript", "-e",
                                f"set volume output volume (output volume of (get volume settings) {'+ ' if mode == 'up' else '- '}{value or 10})"])
                msg = "Громкость изменена."

        else:  # Linux
            if mode == "mute":
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
                msg = "Звук переключён."
            elif mode == "set":
                v = max(0, min(100, int(value)))
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{v}%"])
                msg = f"Громкость: {v}%."
            elif mode == "up":
                v = int(value) if value.isdigit() else 10
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{v}%"])
                msg = f"Громкость +{v}%."
            else:
                v = int(value) if value.isdigit() else 10
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{v}%"])
                msg = f"Громкость -{v}%."

        if record:
            _register_inverse("громкость", _volume, mode, value)
        if player:
            player.write_log(f"SYS: {msg}")
        return msg

    except Exception as e:
        return f"Ошибка управления громкостью: {e}"


def _ps_volume(mode: str, val: int = 10):
    """Windows PowerShell volume control via WScript."""
    if mode == "mute":
        cmd = "(New-Object -comObject WScript.Shell).SendKeys([char]173)"
    elif mode == "set":
        # Нет прямого API — устанавливаем через nircmd если есть, иначе через PowerShell audio
        cmd = f"$obj = new-object -com wscript.shell; for($i=0;$i -lt 50;$i++){{$obj.SendKeys([char]174)}}; for($i=0;$i -lt {val//2};$i++){{$obj.SendKeys([char]175)}}"
    elif mode == "up":
        cmd = f"$obj = new-object -com wscript.shell; for($i=0;$i -lt {max(1,val//4)};$i++){{$obj.SendKeys([char]175)}}"
    else:
        cmd = f"$obj = new-object -com wscript.shell; for($i=0;$i -lt {max(1,val//4)};$i++){{$obj.SendKeys([char]174)}}"
    subprocess.run(["powershell", "-Command", cmd], capture_output=True, timeout=5)


def _brightness(mode: str, value: str, player, record: bool = True) -> str:
    try:
        if _OS == "Windows":
            current = 50  # default guess
            if mode == "up":
                v = min(100, current + int(value or 10))
            elif mode == "down":
                v = max(0, current - int(value or 10))
            else:
                v = int(value or 50)
            cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{v})"
            subprocess.run(["powershell", "-Command", cmd], capture_output=True, timeout=5)
            msg = f"Яркость: {v}%."

        elif _OS == "Darwin":
            # macOS: brightness utility (brew install brightness)
            if mode == "up":
                subprocess.run(["brightness", "+0.1"], capture_output=True)
            elif mode == "down":
                subprocess.run(["brightness", "-0.1"], capture_output=True)
            else:
                v = int(value or 50) / 100
                subprocess.run(["brightness", str(v)], capture_output=True)
            msg = "Яркость изменена."

        else:  # Linux
            # xrandr or brightnessctl
            import shutil
            if shutil.which("brightnessctl"):
                if mode == "up":
                    subprocess.run(["brightnessctl", "set", f"+{value or 10}%"])
                elif mode == "down":
                    subprocess.run(["brightnessctl", "set", f"{value or 10}%-"])
                else:
                    subprocess.run(["brightnessctl", "set", f"{value or 50}%"])
                msg = "Яркость изменена."
            else:
                msg = "Установите brightnessctl для управления яркостью."

        # На Windows яркость выставляется абсолютным значением от ДОГАДКИ
        # (current = 50), а реального прежнего значения система не отдаёт —
        # откатывать нечего, кроме выдумки, поэтому отмену там не регистрируем.
        if record and _OS != "Windows":
            _register_inverse("яркость", _brightness, mode, value)
        if player:
            player.write_log(f"SYS: {msg}")
        return msg

    except Exception as e:
        return f"Ошибка яркости: {e}"


def _lock(player) -> str:
    try:
        if _OS == "Windows":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
        elif _OS == "Darwin":
            subprocess.run(["pmset", "displaysleepnow"])
        else:
            subprocess.run(["loginctl", "lock-session"])
        if player:
            player.write_log("SYS: Экран заблокирован.")
        return "Экран заблокирован."
    except Exception as e:
        return f"Ошибка блокировки: {e}"


def _power(action: str, player) -> str:
    try:
        if any(k in action for k in ["перезагруз", "restart", "reboot"]):
            if _OS == "Windows":
                subprocess.Popen(["shutdown", "/r", "/t", "5"])
            elif _OS == "Darwin":
                subprocess.Popen(["osascript", "-e", "tell app \"System Events\" to restart"])
            else:
                subprocess.Popen(["reboot"])
            return "Перезагружаю компьютер через 5 секунд."
        else:
            if _OS == "Windows":
                subprocess.Popen(["shutdown", "/s", "/t", "5"])
            elif _OS == "Darwin":
                subprocess.Popen(["osascript", "-e", "tell app \"System Events\" to shut down"])
            else:
                subprocess.Popen(["poweroff"])
            return "Выключаю компьютер через 5 секунд."
    except Exception as e:
        return f"Ошибка: {e}"

# ─── Таблица действий ─────────────────────────────────────────────────────────
# Раньше модели предлагалось девять имён, а всё остальное она должна была
# уложить в свободное описание, которое разбиралось поиском подстрок. «Закрой
# вкладку», «верни масштаб», «пролистай вниз» не попадали никуда и получали
# «Действие не распознано».
#
# Имена перечислены и здесь, и в объявлении TOOL: модель выбирает из списка,
# а не сочиняет. Опечатку и синоним чинит difflib за микросекунды — без
# второго обращения к модели, которое стоило бы полного круга по сети.
_КЛАВИШИ = {
    # Правка
    "copy":        "ctrl+c",
    "paste":       "ctrl+v",
    "cut":         "ctrl+x",
    "undo":        "ctrl+z",
    "redo":        "ctrl+y",
    "select_all":  "ctrl+a",
    "save":        "ctrl+s",
    "find":        "ctrl+f",
    "print":       "ctrl+p",
    # Вкладки и навигация
    "new_tab":     "ctrl+t",
    "close_tab":   "ctrl+w",
    "reopen_tab":  "ctrl+shift+t",
    "next_tab":    "ctrl+tab",
    "prev_tab":    "ctrl+shift+tab",
    "refresh":     "f5",
    "hard_refresh": "ctrl+f5",
    "go_back":     "alt+left",
    "go_forward":  "alt+right",
    "address_bar": "ctrl+l",
    # Масштаб
    "zoom_in":     "ctrl+=",
    "zoom_out":    "ctrl+-",
    "zoom_reset":  "ctrl+0",
    # Прокрутка
    "scroll_up":   "pgup",
    "scroll_down": "pgdn",
    "scroll_top":  "ctrl+home",
    "scroll_bottom": "ctrl+end",
    # Одиночные
    "enter":       "enter",
    "escape":      "escape",
    "delete":      "delete",
    "tab":         "tab",
    "full_screen": "f11",
}

# Действия со своей логикой, не сводящиеся к аккорду.
_ОСОБЫЕ = ("volume_up", "volume_down", "volume_set", "mute",
           "brightness_up", "brightness_down", "screenshot", "lock",
           "shutdown", "restart", "type_text", "dark_mode")

# Что человек скажет вместо имени действия. Не замена difflib, а подсказка для
# случаев, где похожесть строк не помогает: «назад» и «go_back» не похожи.
_СИНОНИМЫ = {
    "back": "go_back", "forward": "go_forward", "reload": "refresh",
    "close_window": "close_tab", "fullscreen": "full_screen",
    "screen_shot": "screenshot", "printscreen": "screenshot",
    "zoom_in_page": "zoom_in", "page_up": "scroll_up", "page_down": "scroll_down",
    "home": "scroll_top", "end": "scroll_bottom",
    "select": "select_all", "copy_all": "select_all",
    # Русские имена: модель отвечает по-русски и вполне может так же заполнить
    # параметр — мы это уже видели на режимах поиска.
    "назад": "go_back", "вперёд": "go_forward", "вперед": "go_forward",
    "копировать": "copy", "вставить": "paste", "вырезать": "cut",
    "отменить": "undo", "повторить": "redo", "выделить_всё": "select_all",
    "сохранить": "save", "найти": "find", "обновить": "refresh",
    "новая_вкладка": "new_tab", "закрыть_вкладку": "close_tab",
    "увеличить": "zoom_in", "уменьшить": "zoom_out",
    "вверх": "scroll_up", "вниз": "scroll_down",
    "скриншот": "screenshot", "заблокировать": "lock",
    "весь_экран": "full_screen", "полный_экран": "full_screen",
}

ALL_ACTIONS = tuple(sorted(set(_КЛАВИШИ) | set(_ОСОБЫЕ)))


def _разобрать_действие(имя: str) -> tuple[str, str]:
    """Имя действия → (каноническое имя, пояснение).

    Пустое каноническое имя означает «не понял», и во втором элементе тогда
    лежит подсказка с настоящими именами: тупик без вариантов хуже ошибки."""
    import difflib

    имя = str(имя or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not имя:
        return "", "Не назвали действие"

    if имя in _СИНОНИМЫ:
        return _СИНОНИМЫ[имя], ""
    if имя in ALL_ACTIONS:
        return имя, ""

    # 0.75 подобрано так, чтобы «zom_in» и «scrol_down» чинились, а «музыка»
    # не превращалась во что-нибудь наугад.
    похожие = difflib.get_close_matches(имя, ALL_ACTIONS, n=3, cutoff=0.75)
    if похожие:
        return похожие[0], ""

    подсказка = ", ".join(difflib.get_close_matches(имя, ALL_ACTIONS, n=3, cutoff=0.3)
                          or list(ALL_ACTIONS)[:6])
    return "", f"не знаю действия «{имя}». Похоже на: {подсказка}"


# ─── Точка входа инструмента ──────────────────────────────────────────────────
# Имена действий, которыми пользуется модель, объявлены на английском, а сам
# `computer_settings` понимает русские фразы. Перевод жил в диспетчере main.py —
# то есть словарь действий этого модуля лежал в другом файле, и добавление
# действия требовало правки обоих. Теперь он здесь, рядом с тем, что переводит.
_ACTION_ALIASES = {
    "volume_up":       "увеличить громкость",
    "volume_down":     "уменьшить громкость",
    "mute":            "без звука",
    "brightness_up":   "увеличить яркость",
    "brightness_down": "уменьшить яркость",
    "screenshot":      "скриншот",
    "lock":            "заблокировать",
    "shutdown":        "выключить",
    "restart":         "перезагрузить",
}


def _computer_control_tool(parameters: dict, player=None) -> str:
    """Точка входа инструмента: имя действия → аккорд, значение или своя логика.

    Раньше перевод английских имён в русские фразы жил в диспетчере main.py —
    словарь действий этого модуля лежал в другом файле, и добавление действия
    требовало правки обоих."""
    from actions.keyboard import send_combo, type_text

    сырое = str((parameters or {}).get("action", ""))
    значение = str((parameters or {}).get("value", "") or "")

    действие, беда = _разобрать_действие(сырое)
    if not действие:
        # Не тупик: называем настоящие имена, чтобы следующий вызов попал.
        return f"Сэр, {беда}."

    # 1. Аккорд клавиш — большая часть таблицы.
    аккорд = _КЛАВИШИ.get(действие)
    if аккорд:
        if send_combo(аккорд):
            if player:
                player.write_log(f"SYS: {действие} ({аккорд})")
            return "Готово."
        # Молча не нажатая комбинация выглядит как «Джарвис не послушался».
        return (f"Не смог отправить {аккорд}, сэр — на этой машине некому "
                f"нажимать клавиши (нужен pyautogui).")

    # 2. Ввод текста.
    if действие == "type_text":
        if not значение:
            return "Что напечатать, сэр?"
        if type_text(значение):
            if player:
                player.write_log(f"SYS: напечатано {значение[:40]}")
            return "Напечатал."
        return "Не смог напечатать — некому нажимать клавиши."

    # 3. Тёмная тема — только Windows, и это правка реестра.
    if действие == "dark_mode":
        return _dark_mode(player)

    # 4. Остальное понимает computer_settings по русской фразе.
    русское = _ACTION_ALIASES.get(действие, действие)
    параметры = {"action": русское}
    if значение:
        параметры["value"] = значение
    elif действие in ("volume_up", "volume_down", "brightness_up", "brightness_down"):
        параметры["value"] = "10"
    return computer_settings(параметры, player=player)


def _dark_mode(player=None) -> str:
    """Переключает тёмную тему Windows и кладёт обратное в стек отмены."""
    if _OS != "Windows":
        return "Тёмную тему я умею переключать только в Windows, сэр."

    ключ = (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
    try:
        чтение = subprocess.run(
            ["reg", "query", ключ, "/v", "AppsUseLightTheme"],
            capture_output=True, text=True, timeout=5,
        )
        светлая = "0x1" in (чтение.stdout or "")
    except Exception as exc:
        return f"Не прочитал текущую тему: {exc}"

    def _поставить(светлой: bool) -> bool:
        значение = "1" if светлой else "0"
        try:
            for имя in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                subprocess.run(
                    ["reg", "add", ключ, "/v", имя, "/t", "REG_DWORD",
                     "/d", значение, "/f"],
                    capture_output=True, timeout=5,
                )
            return True
        except Exception as exc:
            _logger.warning("Тема не переключилась: %s", exc)
            return False

    if not _поставить(not светлая):
        return "Не смог переключить тему, сэр."

    # Прежнее значение ПРОЧИТАНО, а не угадано — такую отмену можно обещать.
    push_undo(
        "тёмная тема" if светлая else "светлая тема",
        lambda: "Тему вернул." if _поставить(светлая) else "Тему вернуть не вышло.",
    )
    if player:
        player.write_log("SYS: тема переключена")
    return "Тёмная тема включена." if светлая else "Светлая тема включена."


# ─── Объявление для реестра действий ──────────────────────────────────────────
# Инструмент описывает себя сам: имя, текст для модели, схема аргументов и
# обработчик. core/action_loader.py находит это при запуске — ни списка в
# main.py, ни ветки в диспетчере для нового инструмента больше не нужно.
TOOL = {
    "name": "computer_control",
    "description": (
        "Управляет компьютером: громкость, яркость, скриншот, блокировка, "
        "выключение, а также правка (копировать/вставить/отменить), вкладки "
        "браузера, масштаб, прокрутка, ввод текста и тёмная тема. "
        "Используй для ЛЮБОЙ одиночной команды управления компьютером. "
        "Бери имя действия из списка ниже — не сочиняй своё: опечатку я исправлю, "
        "а выдуманное имя выполнить не смогу. "
        "Выключение и перезагрузка требуют нажатия кнопки на экране — пока её не "
        "нажали, действие НЕ выполнено, не говори что сделал. "
        "Громкость, яркость и тёмную тему можно вернуть инструментом undo."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Точное имя действия: "
                        "address_bar | brightness_down | brightness_up | close_tab | copy | cut | "
                        "dark_mode | delete | enter | escape | find | full_screen | go_back | "
                        "go_forward | hard_refresh | lock | mute | new_tab | next_tab | paste | "
                        "prev_tab | print | redo | refresh | reopen_tab | restart | save | "
                        "screenshot | scroll_bottom | scroll_down | scroll_top | scroll_up | "
                        "select_all | shutdown | tab | type_text | undo | volume_down | "
                        "volume_set | volume_up | zoom_in | zoom_out | zoom_reset "
                ),
            },
            "value": {
                "type": "STRING",
                "description": (
                    "Значение: громкость 0-100 для volume_set, "
                    "шаг для volume_up/brightness_up, текст для type_text"
                ),
            },
        },
        "required": ["action"],
    },
    "handler": _computer_control_tool,
}
