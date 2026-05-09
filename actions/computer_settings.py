"""
Действие: управление компьютером — громкость, яркость, скриншот, и др.
"""

import subprocess
import sys
import os
import platform
import time

_OS = platform.system()


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


def _volume(mode: str, value: str, player) -> str:
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


def _brightness(mode: str, value: str, player) -> str:
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
