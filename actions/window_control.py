"""
Действие: управление окнами и системой через клавиатурные команды Windows.

Все команды используют стандартные Windows-комбинации (Win+X, Alt+X),
которые работают глобально в любом приложении.

Поддержка: Windows (приоритет), macOS / Linux (best effort через pyautogui).
"""

import platform
import subprocess
import time

_OS = platform.system()

# ─── pyautogui (опционально) ──────────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False


# ─── Windows VK codes ─────────────────────────────────────────────────────────
VK_LWIN     = 0x5B
VK_MENU     = 0x12  # Alt
VK_CONTROL  = 0x11
VK_SHIFT    = 0x10
VK_TAB      = 0x09
VK_F4       = 0x73
VK_ESCAPE   = 0x1B
VK_LEFT     = 0x25
VK_UP       = 0x26
VK_RIGHT    = 0x27
VK_DOWN     = 0x28
VK_D        = 0x44
VK_E        = 0x45
VK_I        = 0x49
VK_M        = 0x4D
VK_R        = 0x52

KEYEVENTF_KEYUP = 0x02


def _key_down(vk: int) -> None:
    """Нажать клавишу (через Win32 API)."""
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)


def _key_up(vk: int) -> None:
    """Отпустить клавишу."""
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _press(vk: int) -> bool:
    """Кратко нажать клавишу."""
    try:
        _key_down(vk)
        time.sleep(0.05)
        _key_up(vk)
        return True
    except Exception:
        return False


def _combo(*vks: int) -> bool:
    """
    Нажать комбинацию клавиш (например Ctrl+Alt+Del).
    Нажимаем все по очереди, отпускаем в обратном порядке.
    """
    try:
        # Нажимаем все
        for vk in vks:
            _key_down(vk)
            time.sleep(0.03)
        time.sleep(0.05)
        # Отпускаем в обратном порядке
        for vk in reversed(vks):
            _key_up(vk)
            time.sleep(0.03)
        return True
    except Exception:
        return False


def _pyautogui_hotkey(*keys: str) -> bool:
    """Fallback для не-Windows систем через pyautogui."""
    if not _HAS_PYAUTOGUI:
        return False
    try:
        pyautogui.hotkey(*keys)
        return True
    except Exception:
        return False


# ─── Действия ─────────────────────────────────────────────────────────────────
def _close_window(player=None) -> str:
    """Закрыть активное окно (Alt+F4)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_MENU, VK_F4)
    else:
        ok = _pyautogui_hotkey("alt", "f4")

    if ok:
        if player:
            player.write_log("SYS: ✕ Окно закрыто")
        return "Окно закрыто, сэр."
    return "Не получилось закрыть окно, сэр."


def _minimize_window(player=None) -> str:
    """Свернуть активное окно (Win+Down)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_DOWN)
    else:
        ok = _pyautogui_hotkey("win", "down")

    if ok:
        if player:
            player.write_log("SYS: ⬇ Окно свёрнуто")
        return "Свернул окно, сэр."
    return "Не получилось свернуть окно, сэр."


def _maximize_window(player=None) -> str:
    """Развернуть активное окно (Win+Up)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_UP)
    else:
        ok = _pyautogui_hotkey("win", "up")

    if ok:
        if player:
            player.write_log("SYS: ⬆ Окно развёрнуто")
        return "Развернул окно, сэр."
    return "Не получилось развернуть окно, сэр."


def _snap_left(player=None) -> str:
    """Прижать окно к левой половине экрана (Win+Left)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_LEFT)
    else:
        ok = _pyautogui_hotkey("win", "left")
    return "Окно слева, сэр." if ok else "Не получилось, сэр."


def _snap_right(player=None) -> str:
    """Прижать окно к правой половине экрана (Win+Right)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_RIGHT)
    else:
        ok = _pyautogui_hotkey("win", "right")
    return "Окно справа, сэр." if ok else "Не получилось, сэр."


def _switch_window(player=None) -> str:
    """Переключиться на следующее окно (Alt+Tab)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_MENU, VK_TAB)
    else:
        ok = _pyautogui_hotkey("alt", "tab")

    if ok:
        if player:
            player.write_log("SYS: ⇄ Переключение окон")
        return "Переключил окно, сэр."
    return "Не получилось переключить, сэр."


def _show_desktop(player=None) -> str:
    """Показать рабочий стол (Win+D)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_D)
    else:
        ok = _pyautogui_hotkey("win", "d")

    if ok:
        if player:
            player.write_log("SYS: 🖥 Рабочий стол")
        return "Рабочий стол, сэр."
    return "Не получилось, сэр."


def _minimize_all(player=None) -> str:
    """Свернуть все окна (Win+M)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_M)
    else:
        ok = _pyautogui_hotkey("win", "m")

    if ok:
        if player:
            player.write_log("SYS: ⬇⬇ Все окна свёрнуты")
        return "Все окна свёрнуты, сэр."
    return "Не получилось, сэр."


def _open_explorer(player=None) -> str:
    """Открыть Проводник (Win+E)."""
    ok = False
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_E)
    else:
        # На macOS/Linux запускаем файловый менеджер
        try:
            if _OS == "Darwin":
                subprocess.Popen(["open", "."])
            else:
                subprocess.Popen(["xdg-open", "."])
            ok = True
        except Exception:
            pass

    if ok:
        if player:
            player.write_log("SYS: 📁 Проводник")
        return "Открыл проводник, сэр."
    return "Не получилось открыть проводник, сэр."


def _task_manager(player=None) -> str:
    """Открыть диспетчер задач (Ctrl+Shift+Esc)."""
    if _OS == "Windows":
        ok = _combo(VK_CONTROL, VK_SHIFT, VK_ESCAPE)
        if ok:
            if player:
                player.write_log("SYS: 📊 Диспетчер задач")
            return "Диспетчер задач, сэр."
    return "Диспетчер задач доступен только на Windows, сэр."


def _open_settings(player=None) -> str:
    """Открыть Параметры Windows (Win+I)."""
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_I)
        if ok:
            if player:
                player.write_log("SYS: ⚙ Параметры")
            return "Параметры, сэр."
    return "Не получилось, сэр."


def _open_run(player=None) -> str:
    """Открыть Выполнить (Win+R)."""
    if _OS == "Windows":
        ok = _combo(VK_LWIN, VK_R)
        if ok:
            return "Выполнить, сэр."
    return "Не получилось, сэр."


def _activate_window_by_title(title_part: str, player=None) -> str:
    """
    Активировать окно по части заголовка (например "chrome", "spotify").
    Использует pygetwindow.
    """
    if not title_part.strip():
        return "Укажите название приложения, сэр."

    try:
        import pygetwindow as gw
        title_lower = title_part.strip().lower()
        for win in gw.getAllWindows():
            if title_lower in (win.title or "").lower():
                try:
                    if win.isMinimized:
                        win.restore()
                    win.activate()
                    if player:
                        player.write_log(f"SYS: ◉ Активировано: {win.title}")
                    return f"Открыл {title_part}, сэр."
                except Exception:
                    # Иногда нужен минимайз+ресторе для активации
                    try:
                        win.minimize()
                        time.sleep(0.1)
                        win.restore()
                        return f"Открыл {title_part}, сэр."
                    except Exception:
                        pass
        return f"Окно «{title_part}» не найдено, сэр."
    except ImportError:
        return "Модуль pygetwindow не установлен, сэр."
    except Exception as e:
        return f"Ошибка: {str(e)[:80]}"


# ─── Публичная точка входа ────────────────────────────────────────────────────
def window_control(parameters: dict, player=None) -> str:
    """
    Главная точка входа для tool 'window_control'.

    parameters:
        action: close | minimize | maximize | snap_left | snap_right |
                switch | show_desktop | minimize_all | open_explorer |
                task_manager | settings | run | activate
        target: для action=activate — название окна (например "Chrome")
    """
    action = (parameters.get("action") or "").strip().lower()
    target = (parameters.get("target") or "").strip()

    # ── Closing ───────────────────────────────────────────────────────────────
    if action in ("close", "close_window", "закрыть"):
        return _close_window(player)

    # ── Minimize / Maximize ───────────────────────────────────────────────────
    elif action in ("minimize", "свернуть"):
        return _minimize_window(player)

    elif action in ("maximize", "развернуть"):
        return _maximize_window(player)

    elif action in ("minimize_all", "свернуть_все"):
        return _minimize_all(player)

    # ── Snap ──────────────────────────────────────────────────────────────────
    elif action in ("snap_left", "влево"):
        return _snap_left(player)

    elif action in ("snap_right", "вправо"):
        return _snap_right(player)

    # ── Switch / Desktop ──────────────────────────────────────────────────────
    elif action in ("switch", "switch_window", "alt_tab", "переключить"):
        return _switch_window(player)

    elif action in ("show_desktop", "desktop", "рабочий_стол"):
        return _show_desktop(player)

    # ── Quick launchers ───────────────────────────────────────────────────────
    elif action in ("open_explorer", "explorer", "проводник"):
        return _open_explorer(player)

    elif action in ("task_manager", "диспетчер"):
        return _task_manager(player)

    elif action in ("settings", "параметры"):
        return _open_settings(player)

    elif action in ("run", "выполнить"):
        return _open_run(player)

    # ── Activate specific window ──────────────────────────────────────────────
    elif action in ("activate", "focus", "переключить_на"):
        return _activate_window_by_title(target, player)

    return f"Не понял команду: «{action}»."
