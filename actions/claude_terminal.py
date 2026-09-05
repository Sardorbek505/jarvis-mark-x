"""
ДЖАРВИС — Удалённое управление и ввод в терминал Claude Code / консоль ИИ.

Позволяет с телефона (через Telegram-бот) или голосом на ПК:
1. Проверить блокировку экрана Windows и разблокировать при необходимости.
2. Найти активное окно терминала с Claude Code (по названию проекта или процесса).
3. Вывести окно на передний план.
4. Проверить происходящее на экране глазами через компьютерное зрение (Gemini 2.5 Flash Vision).
5. Напечатать нужную команду/текст через буфер обмена (гарантия точной раскладки) и нажать Enter.
6. Сделать контрольный скриншот и отправить подтверждение с фото пользователю в Telegram.
"""
import base64
import ctypes
import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Optional, Tuple

import pyperclip

logger = logging.getLogger("jarvis-claude-terminal")

_UNLOCK_FILE = Path(__file__).resolve().parent.parent / "config" / "unlock_password.txt"

# Win32 константы
SW_RESTORE = 9
SW_SHOW = 5
VK_SPACE = 0x20
VK_RETURN = 0x0D
VK_CONTROL = 0x11
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def _attach_default_desktop():
    """Привязывает текущий поток к интерактивному рабочему столу пользователя."""
    try:
        hdesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception as exc:
        logger.debug("Не удалось переключить десктоп: %s", exc)


def is_screen_locked() -> bool:
    """Проверяет, заблокирован ли экран Windows (Lock Screen / Logon)."""
    try:
        desk = user32.OpenInputDesktop(0, False, 0x0100)
        if not desk:
            return True
        user32.CloseDesktop(desk)
        return False
    except Exception:
        return False


def read_unlock_password() -> str:
    """Считывает пароль или PIN-код для разблокировки экрана."""
    try:
        if _UNLOCK_FILE.exists():
            return _UNLOCK_FILE.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.debug("Не удалось прочитать пароль: %s", exc)
    return ""


def save_unlock_password(pwd: str) -> bool:
    """Сохраняет пароль или PIN-код в config/unlock_password.txt."""
    try:
        _UNLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _UNLOCK_FILE.write_text(pwd.strip(), encoding="utf-8")
        return True
    except Exception as e:
        logger.error("Ошибка сохранения пароля: %s", e)
        return False


def _send_vk(vk: int):
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _type_unicode_char(ch: str):
    code = ord(ch)
    user32.keybd_event(0, code, 0x0004, 0)          # KEYEVENTF_UNICODE
    user32.keybd_event(0, code, 0x0004 | 0x0002, 0) # KEYEVENTF_UNICODE | KEYEVENTF_KEYUP


def unlock_screen() -> Tuple[bool, str]:
    """Пытается разбудить экран и разблокировать Windows."""
    if not is_screen_locked():
        return True, "Экран уже разблокирован."

    _attach_default_desktop()
    pwd = read_unlock_password()

    # 1. Будим экран нажатием Пробела
    _send_vk(VK_SPACE)
    time.sleep(0.5)

    # 2. Если задан пароль — вводим его
    if pwd:
        for ch in pwd:
            _type_unicode_char(ch)
            time.sleep(0.02)
        time.sleep(0.2)
        _send_vk(VK_RETURN)
        time.sleep(1.0)
    else:
        # Без пароля часто достаточно повторного Space или Enter
        _send_vk(VK_RETURN)
        time.sleep(0.8)

    if not is_screen_locked():
        return True, "Экран успешно разблокирован."

    return False, (
        "Экран заблокирован Windows. Я отправил сигнал пробуждения, "
        "но Windows ожидает пароль/PIN. Вы можете сохранить PIN командой /unlock_pwd <пин> в боте."
    )


def find_terminal_window(hint: Optional[str] = None):
    """Находит окно терминала Claude Code или консоли."""
    _attach_default_desktop()
    try:
        import pygetwindow as gw
    except ImportError:
        logger.error("pygetwindow не установлен")
        return None

    windows = gw.getAllWindows()
    visible_windows = [w for w in windows if w.title and w.title.strip()]

    # 1. Если задана подсказка пользователя (например 'smart store' или 'хром')
    if hint:
        h = hint.strip().lower()
        for w in visible_windows:
            if h in w.title.lower():
                return w

    # 2. Поиск окон с проектами пользователя или Claude Code
    # Приоритет проекту пользователя из запроса (Smart Store)
    priority_keywords = [
        "smart store",
        "smartstore",
        "claude",
        "opus",
        "agent",
    ]
    for kw in priority_keywords:
        for w in visible_windows:
            if kw in w.title.lower():
                return w

    # 3. Любой открытый эмулятор терминала / консоль
    terminal_keywords = [
        "terminal",
        "командная строка",
        "command prompt",
        "powershell",
        "cmd.exe",
        "wsl",
        "bash",
        "mingw",
        "conemu",
    ]
    for kw in terminal_keywords:
        for w in visible_windows:
            if kw in w.title.lower():
                return w

    return None


def force_focus_window(hwnd: int) -> bool:
    """Принудительно разворачивает и фокусирует окно в Windows."""
    try:
        _attach_default_desktop()
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)

        # Разблокировка ограничения SetForegroundWindow через AttachThreadInput
        cur_thread = kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0

        if fg_thread and fg_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fg_thread, True)

        user32.SetForegroundWindow(hwnd)

        if fg_thread and fg_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fg_thread, False)

        time.sleep(0.3)
        return True
    except Exception as exc:
        logger.warning("Ошибка активации окна %s: %s", hwnd, exc)
        return False


def type_text_to_terminal(text: str, press_enter: bool = True) -> bool:
    """
    Вставляет текст в текущее активное окно через буфер обмена (Ctrl+V)
    и нажимает Enter. Гарантирует корректность всех языков и спецсимволов.
    """
    try:
        # Сохраняем исходный буфер и кладём наш текст
        old_clip = None
        try:
            old_clip = pyperclip.paste()
        except Exception:
            pass

        pyperclip.copy(text)
        time.sleep(0.05)

        # Отправляем Ctrl+V
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(ord('V'), 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(ord('V'), 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.15)

        if press_enter:
            _send_vk(VK_RETURN)
            time.sleep(0.1)

        # Возвращаем исходный буфер через полсекунды
        if old_clip is not None:
            time.sleep(0.3)
            try:
                pyperclip.copy(old_clip)
            except Exception:
                pass

        return True
    except Exception as e:
        logger.error("Ошибка ввода текста: %s", e)
        return False


def capture_screenshot_bytes() -> Optional[bytes]:
    """Делает снимок экрана через нативный захват."""
    from actions.vision import capture_screen_jpeg
    b = capture_screen_jpeg(max_size=1280, quality=85)
    if b:
        return b

    # Фолбэк через PowerShell
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        home = os.path.expanduser("~")
        path = os.path.join(home, f"Desktop/temp_screen_{ts}.png")
        ps_cmd = (
            'Add-Type -AssemblyName System.Windows.Forms; '
            'Add-Type -AssemblyName System.Drawing; '
            '$screen = [System.Windows.Forms.Screen]::PrimaryScreen; '
            '$bmp = New-Object System.Drawing.Bitmap($screen.Bounds.Width, $screen.Bounds.Height); '
            '$g = [System.Drawing.Graphics]::FromImage($bmp); '
            '$g.CopyFromScreen(0,0,0,0,$bmp.Size); '
            f'$bmp.Save("{path}"); '
            '$g.Dispose(); $bmp.Dispose()'
        )
        subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=8)
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            try:
                os.remove(path)
            except OSError:
                pass
            return data
    except Exception as exc:
        logger.error("Ошибка PowerShell скриншота: %s", exc)

    return None


def verify_screen_with_eyes(image_bytes: bytes) -> str:
    """Анализирует экран через Gemini 2.5 Flash Vision."""
    try:
        from actions.vision import analyze_vision
        prompt = (
            "Определи, открыто ли окно терминала с Claude Code или консолью ИИ на экране. "
            "В каком оно сейчас состоянии: готово ли принимать ввод (виден prompt >), "
            "идёт ли выполнение задачи, либо исчерпан лимит сессии / видна ошибка? "
            "Сформулируй вердикт в одном коротком предложении."
        )
        verdict = analyze_vision(prompt=prompt, image_bytes=image_bytes)
        return verdict.strip()
    except Exception as e:
        logger.warning("Зрение недоступно: %s", e)
        return "Зрительный анализ пропущен."


def execute_claude_typing(
    text: str,
    press_enter: bool = True,
    target_hint: Optional[str] = None
) -> dict:
    """
    Главная процедура удалённого ввода в терминал:
    1. Проверка экрана / разблокировка.
    2. Поиск окна Claude / терминала.
    3. Активация и фокус.
    4. Проверка состояния через зрение Gemini.
    5. Печать текста (Ctrl+V + Enter).
    6. Контрольный скриншот и отчёт.
    """
    text = (text or "").strip()
    if not text:
        return {"text": "❌ Не указан текст для отправки в Claude.", "image_b64": None}

    # 1. Проверяем блокировку экрана
    if is_screen_locked():
        ok, unlock_msg = unlock_screen()
        if not ok:
            return {"text": f"🔒 {unlock_msg}", "image_b64": None}
        time.sleep(0.5)

    # 2. Ищем окно терминала
    win = find_terminal_window(hint=target_hint)
    if not win:
        return {
            "text": (
                "❌ Окно терминала с Claude Code не найдено на рабочем столе.\n"
                "Убедитесь, что терминал запущен на ПК (например, Smart Store или консоль)."
            ),
            "image_b64": None
        }

    target_title = win.title
    logger.info("Найдено целевое окно: %s (HWND %s)", target_title, win._hWnd)

    # 3. Выводим окно на передний план
    force_focus_window(win._hWnd)
    time.sleep(0.4)

    # 4. Проверяем глазами перед вводом
    pre_bytes = capture_screenshot_bytes()
    vision_verdict = ""
    if pre_bytes:
        vision_verdict = verify_screen_with_eyes(pre_bytes)

    # 5. Печатаем текст
    typed_ok = type_text_to_terminal(text, press_enter=press_enter)
    if not typed_ok:
        return {"text": f"❌ Не удалось напечатать текст в окно {target_title}.", "image_b64": None}

    # 6. Делаем контрольный скриншот после ввода
    time.sleep(0.8)
    post_bytes = capture_screenshot_bytes()
    image_b64 = None
    if post_bytes:
        image_b64 = base64.b64encode(post_bytes).decode("ascii")

    report = (
        f"✅ Написал в **{target_title}**:\n"
        f"«{text}»\n\n"
        f"👁 **Глазами перед вводом**: {vision_verdict}\n"
        f"⚡ Команда отправлена (Enter: {'Да' if press_enter else 'Нет'})."
    )

    return {
        "text": report,
        "image_b64": image_b64,
        "status": "success"
    }
