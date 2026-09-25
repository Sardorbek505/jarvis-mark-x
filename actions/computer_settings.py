"""
Действие: управление компьютером — громкость, яркость, скриншот, и др.
"""

import os
import platform
import re
import subprocess
import time

_OS = platform.system()


_NO_WINDOW = 0x08000000 if _OS == "Windows" else 0   # CREATE_NO_WINDOW: без мелькающей консоли

# Точные действия (их шлёт Gemini по схеме computer_control).
_ACTIONS = {
    "volume_up": ("volume", "up"), "volume_down": ("volume", "down"),
    "volume_set": ("volume", "set"), "mute": ("volume", "mute"), "unmute": ("volume", "unmute"),
    "brightness_up": ("brightness", "up"), "brightness_down": ("brightness", "down"),
    "brightness_set": ("brightness", "set"),
}


def parse_level(value, default: int | None = None) -> int | None:
    """«50», «50%», «на 10», «до 30», «максимум», «половина» → число 0..100."""
    text = str(value or "").strip().lower()
    if not text:
        return default
    if any(w in text for w in ("макс", "max", "полн", "100")):
        return 100
    if any(w in text for w in ("мин", "min", "ноль")):
        return 0
    if "полов" in text or "half" in text:
        return 50
    m = re.search(r"\d{1,3}", text)
    return max(0, min(100, int(m.group()))) if m else default


def computer_settings(parameters: dict, response=None, player=None) -> str:
    action = (parameters.get("action") or parameters.get("description") or "").lower().strip()
    value = parameters.get("value")

    if action in _ACTIONS:
        what, mode = _ACTIONS[action]
        return _volume(mode, value, player) if what == "volume" else _brightness(mode, value, player)

    # --- СКРИНШОТ ---
    if any(k in action for k in ["скриншот", "screenshot", "снимок экрана", "захват экрана"]):
        return _screenshot(player)

    # --- ЯРКОСТЬ --- (раньше громкости: «сделай экран ярче»)
    if any(k in action for k in ["яркость", "brightness", "ярче", "темнее"]):
        if any(k in action for k in ["уменьш", "down", "убав", "decrease", "темн", "меньше"]):
            return _brightness("down", value, player)
        if any(k in action for k in ["увелич", "up", "добав", "increase", "ярче", "больше"]):
            return _brightness("up", value, player)
        return _brightness("set" if parse_level(value) is not None else "up", value, player)

    # --- ГРОМКОСТЬ ---
    if any(k in action for k in ["громкость", "volume", "звук", "громче", "тише"]):
        if any(k in action for k in ["включи звук", "unmute", "верни звук"]):
            return _volume("unmute", value, player)
        if any(k in action for k in ["тише", "уменьш", "убав", "down", "decrease"]):
            return _volume("down", value, player)
        if any(k in action for k in ["громче", "увелич", "up", "increase"]):
            return _volume("up", value, player)
        if any(k in action for k in ["выкл", "mute", "без звука", "отключ"]):
            return _volume("mute", value, player)
        return _volume("set" if parse_level(value) is not None else "up", value, player)

    # --- БЛОКИРОВКА ---
    if any(k in action for k in ["заблок", "lock"]):
        return _lock(player)

    # --- ВЫКЛЮЧЕНИЕ ---
    if any(k in action for k in ["выключ", "shutdown", "перезагруз", "restart", "reboot"]):
        return _power(action, player)

    return f"Действие не распознано: {action}"


def _desktop_dir() -> str:
    """Настоящий рабочий стол. При OneDrive это не ~/Desktop — там папки
    может не быть вовсе, и скриншот «сохранялся» в никуда."""
    if _OS == "Windows":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:
                return buf.value            # CSIDL_DESKTOPDIRECTORY
        except Exception:
            pass
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return desktop if os.path.isdir(desktop) else os.path.expanduser("~")


def _screenshot(player) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_desktop_dir(), f"screenshot_{ts}.png")
    try:
        try:
            # mss снимает все мониторы в реальных пикселях — без обрезки при
            # масштабе экрана 125-150%, которой страдал снимок через PowerShell.
            import mss
            import mss.tools
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[0])
                mss.tools.to_png(shot.rgb, shot.size, output=path)
        except ImportError:
            if _OS == "Darwin":
                subprocess.run(["screencapture", path], timeout=5)
            else:
                for tool in ["scrot", "gnome-screenshot", "import"]:
                    result = subprocess.run([tool, path], capture_output=True, timeout=5)
                    if result.returncode == 0:
                        break

        if not os.path.isfile(path):
            return "Скриншот не получился, сэр."
        if player:
            player.write_log(f"FILE: Скриншот → {path}")
        return f"Скриншот сохранён: {path}"
    except Exception as e:
        return f"Ошибка скриншота: {e}"


def _endpoint():
    """Громкость устройства вывода по умолчанию (pycaw, CoreAudio).

    Раньше громкость «жали» медиа-клавишами через PowerShell: окно консоли
    мелькало, первый запуск не укладывался в 5 с, а «громкость 50»
    превращалось в «+50%». Теперь уровень читается и ставится напрямую."""
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    ep = getattr(dev, "EndpointVolume", None)          # pycaw ≥ 20240316
    if ep is None:                                      # старый API
        from comtypes import CLSCTX_ALL, POINTER, cast
        ep = cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                  POINTER(IAudioEndpointVolume))
    return ep


def _com(fn):
    """COM в рабочем потоке нужно инициализировать — инструменты зовутся
    из пула потоков, и без этого pycaw падал с «CoInitialize не вызван»."""
    import comtypes
    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    try:
        return fn()
    finally:
        try:
            comtypes.CoUninitialize()
        except OSError:
            pass


def _media_key(vk: int, times: int = 1):
    """Запасной путь без pycaw: медиа-клавиши прямо из процесса (без
    PowerShell). Один шаг Windows — 2%."""
    import ctypes
    for _ in range(max(1, times)):
        ctypes.windll.user32.keybd_event(vk, 0, 1, 0)          # KEYEVENTF_EXTENDEDKEY
        ctypes.windll.user32.keybd_event(vk, 0, 1 | 2, 0)      # + KEYEVENTF_KEYUP


def _volume(mode: str, value, player) -> str:
    step = parse_level(value, 10) or 10
    target = parse_level(value)
    try:
        if _OS == "Windows":
            try:
                msg = _com(lambda: _volume_pycaw(mode, step, target))
            except Exception as exc:
                # pycaw не завёлся — жмём медиа-клавиши, честно без точного числа
                vk = {"up": 0xAF, "down": 0xAE}.get(mode)
                if mode in ("mute", "unmute"):
                    _media_key(0xAD)
                    msg = "Переключил звук."
                elif vk:
                    _media_key(vk, max(1, step // 2))
                    msg = "Громкость " + ("выше." if mode == "up" else "ниже.")
                else:
                    return f"Не получилось поставить громкость: {exc}"

        elif _OS == "Darwin":
            if mode in ("mute", "unmute"):
                subprocess.run(["osascript", "-e",
                                f"set volume {'with' if mode == 'mute' else 'without'} output muted"])
                msg = "Звук выключен." if mode == "mute" else "Звук включён."
            elif mode == "set":
                subprocess.run(["osascript", "-e", f"set volume output volume {target or 50}"])
                msg = f"Громкость {target or 50}%."
            else:
                sign = "+" if mode == "up" else "-"
                subprocess.run(["osascript", "-e",
                                f"set volume output volume (output volume of (get volume settings) {sign} {step})"])
                msg = "Громкость изменена."

        else:  # Linux
            if mode in ("mute", "unmute"):
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mode == "mute" else "0"])
                msg = "Звук выключен." if mode == "mute" else "Звук включён."
            elif mode == "set":
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{target or 50}%"])
                msg = f"Громкость {target or 50}%."
            else:
                sign = "+" if mode == "up" else "-"
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{step}%"])
                msg = "Громкость изменена."

        if player:
            player.write_log(f"SYS: {msg}")
        return msg

    except Exception as e:
        return f"Ошибка управления громкостью: {e}"


def _volume_pycaw(mode: str, step: int, target: int | None) -> str:
    ep = _endpoint()
    if mode == "mute":
        ep.SetMute(1, None)
        return "Звук выключен."
    if mode == "unmute":
        ep.SetMute(0, None)
        return f"Звук включён, громкость {round(ep.GetMasterVolumeLevelScalar() * 100)}%."
    now = round(ep.GetMasterVolumeLevelScalar() * 100)
    if mode == "set":
        new = target if target is not None else 50
    elif mode == "up":
        new = min(100, now + step)
    else:
        new = max(0, now - step)
    ep.SetMasterVolumeLevelScalar(new / 100, None)
    if new > 0:
        ep.SetMute(0, None)            # «громче» при выключенном звуке — включаем
    return f"Громкость {new}%." if new != now else f"Громкость уже {now}%."


def get_volume() -> int | None:
    """Текущая громкость, % (для самопроверки и карточки)."""
    if _OS != "Windows":
        return None
    return _com(lambda: round(_endpoint().GetMasterVolumeLevelScalar() * 100))


def _brightness(mode: str, value, player) -> str:
    step = parse_level(value, 10) or 10
    target = parse_level(value)
    try:
        if _OS == "Windows":
            msg = _brightness_windows(mode, step, target)

        elif _OS == "Darwin":
            if mode == "up":
                subprocess.run(["brightness", "+0.1"], capture_output=True)
            elif mode == "down":
                subprocess.run(["brightness", "-0.1"], capture_output=True)
            else:
                subprocess.run(["brightness", str((target or 50) / 100)], capture_output=True)
            msg = "Яркость изменена."

        else:  # Linux
            import shutil
            if not shutil.which("brightnessctl"):
                return "Установите brightnessctl для управления яркостью."
            arg = {"up": f"+{step}%", "down": f"{step}%-"}.get(mode, f"{target or 50}%")
            subprocess.run(["brightnessctl", "set", arg])
            msg = "Яркость изменена."

        if player:
            player.write_log(f"SYS: {msg}")
        return msg

    except Exception as e:
        return f"Ошибка яркости: {e}"


def _brightness_windows(mode: str, step: int, target: int | None) -> str:
    """Ноутбук — через WMI, внешний монитор — через DDC/CI. Оба умеет
    screen_brightness_control; раньше был только WMI, и на обычном
    мониторе яркость не менялась никогда."""
    try:
        import screen_brightness_control as sbc
        levels = sbc.get_brightness()
        if levels:
            now = int(levels[0])
            new = _next_level(mode, now, step, target)
            sbc.set_brightness(new)
            return f"Яркость {new}%." if new != now else f"Яркость уже {now}%."
    except ImportError:
        pass
    except Exception as exc:
        # Монитор без DDC/CI (или он выключен в меню монитора)
        return ("Этот монитор не даёт менять яркость программно, сэр. "
                f"Включите DDC/CI в меню монитора. ({exc})")

    read = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"],
        capture_output=True, timeout=10, creationflags=_NO_WINDOW)
    out = read.stdout.decode("ascii", "ignore").strip().splitlines()
    if read.returncode != 0 or not out or not out[0].strip().isdigit():
        return "Этот монитор не даёт менять яркость программно, сэр."
    now = int(out[0].strip())
    new = _next_level(mode, now, step, target)
    cmd = f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{new})"
    res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                         capture_output=True, timeout=10, creationflags=_NO_WINDOW)
    if res.returncode != 0:
        return "Не получилось изменить яркость, сэр."
    return f"Яркость {new}%."


def _next_level(mode: str, now: int, step: int, target: int | None) -> int:
    if mode == "up":
        return min(100, now + step)
    if mode == "down":
        return max(0, now - step)
    return target if target is not None else 50


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
                subprocess.Popen(["shutdown", "/r", "/t", "5"], creationflags=_NO_WINDOW)
            elif _OS == "Darwin":
                subprocess.Popen(["osascript", "-e", "tell app \"System Events\" to restart"])
            else:
                subprocess.Popen(["reboot"])
            return "Перезагружаю компьютер через 5 секунд."
        else:
            if _OS == "Windows":
                subprocess.Popen(["shutdown", "/s", "/t", "5"], creationflags=_NO_WINDOW)
            elif _OS == "Darwin":
                subprocess.Popen(["osascript", "-e", "tell app \"System Events\" to shut down"])
            else:
                subprocess.Popen(["poweroff"])
            return "Выключаю компьютер через 5 секунд."
    except Exception as e:
        return f"Ошибка: {e}"
