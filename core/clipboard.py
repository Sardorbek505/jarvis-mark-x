"""
Буфер обмена: прочитать по просьбе и положить туда текст.

ПОЧЕМУ БЕЗ СЛЕЖЕНИЯ ЗА БУФЕРОМ
    Соседний проект показывает панель при каждом копировании: скопировал —
    получил кнопки «перевести», «пересказать», «объяснить». Красиво, и мы
    этого сознательно НЕ делаем.

    Следить за буфером — значит читать всё, что человек копирует, включая
    пароли из менеджера паролей, коды из банковских сообщений и чужую
    переписку. Ассистент, который видит это постоянно, отличается от
    ассистента, который видит это по просьбе, ровно тем, что первому нельзя
    доверять буфер вообще.

    Поэтому буфер читается ТОЛЬКО тогда, когда человек сказал «переведи, что
    я скопировал». Это тот же сценарий, но без постоянного подглядывания.

ЧЕМ ЧИТАЕТСЯ
    Тем, что есть в системе: ctypes на Windows (точно, без возни с
    кодировками консоли), pbcopy/pbpaste на macOS, xclip, xsel или
    wl-clipboard на Linux. Нет ничего — так и говорим, а не возвращаем
    пустую строку, неотличимую от пустого буфера.
"""

import logging
import platform
import shutil
import subprocess

_logger = logging.getLogger(__name__)

_OS = platform.system()

_ТАЙМАУТ = 10

# Сколько текста берём из буфера. Туда попадает и мегабайтная таблица;
# уносить её целиком в модель — это и деньги, и переполненный контекст.
ПРЕДЕЛ = 8000


class БуферНедоступен(RuntimeError):
    """Читать и писать нечем. Отличается от пустого буфера намеренно."""


# ─── Linux ───────────────────────────────────────────────────────────────────
# Порядок: Wayland, затем X11. Обратный порядок в сеансе Wayland попал бы в
# xclip, который там работает только с XWayland — то есть мимо буфера.
_LINUX_ЧТЕНИЕ = (
    ("wl-paste", ["wl-paste", "--no-newline"]),
    ("xclip",    ["xclip", "-selection", "clipboard", "-out"]),
    ("xsel",     ["xsel", "--clipboard", "--output"]),
)

_LINUX_ЗАПИСЬ = (
    ("wl-copy", ["wl-copy"]),
    ("xclip",   ["xclip", "-selection", "clipboard", "-in"]),
    ("xsel",    ["xsel", "--clipboard", "--input"]),
)


def _первая_доступная(варианты):
    for команда, аргументы in варианты:
        if shutil.which(команда):
            return аргументы
    return None


# ─── Windows ─────────────────────────────────────────────────────────────────

def _windows_read() -> str:
    import ctypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if not user32.OpenClipboard(0):
        raise БуферНедоступен("буфер занят другим приложением")
    try:
        ручка = user32.GetClipboardData(13)          # CF_UNICODETEXT
        if not ручка:
            return ""                                # в буфере не текст
        указатель = kernel32.GlobalLock(ручка)
        if not указатель:
            return ""
        try:
            return ctypes.c_wchar_p(указатель).value or ""
        finally:
            kernel32.GlobalUnlock(ручка)
    finally:
        user32.CloseClipboard()


def _windows_write(текст: str) -> None:
    import ctypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if not user32.OpenClipboard(0):
        raise БуферНедоступен("буфер занят другим приложением")
    try:
        user32.EmptyClipboard()
        размер = (len(текст) + 1) * ctypes.sizeof(ctypes.c_wchar)
        # GMEM_MOVEABLE: владельцем памяти после SetClipboardData становится
        # система, освобождать её самим нельзя.
        ручка = kernel32.GlobalAlloc(0x0002, размер)
        указатель = kernel32.GlobalLock(ручка)
        ctypes.memmove(указатель, ctypes.create_unicode_buffer(текст), размер)
        kernel32.GlobalUnlock(ручка)
        user32.SetClipboardData(13, ручка)
    finally:
        user32.CloseClipboard()


# ─── Общий вход ──────────────────────────────────────────────────────────────

def read() -> str:
    """Текст из буфера. Пустая строка — буфер пуст или там не текст."""
    if _OS == "Windows":
        return _windows_read()[:ПРЕДЕЛ]

    if _OS == "Darwin":
        команда = ["pbpaste"]
    else:
        команда = _первая_доступная(_LINUX_ЧТЕНИЕ)
        if команда is None:
            raise БуферНедоступен(
                "нет ни wl-clipboard, ни xclip, ни xsel")

    try:
        итог = subprocess.run(команда, capture_output=True, text=True,
                              timeout=_ТАЙМАУТ)
    except FileNotFoundError as exc:
        raise БуферНедоступен(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise БуферНедоступен("буфер не ответил") from exc

    # xclip на пустом буфере возвращает ненулевой код — это не отказ.
    return (итог.stdout or "")[:ПРЕДЕЛ]


def write(текст: str) -> None:
    """Кладёт текст в буфер. Исключение — значит НЕ положили."""
    текст = текст or ""
    if _OS == "Windows":
        _windows_write(текст)
        return

    if _OS == "Darwin":
        команда = ["pbcopy"]
    else:
        команда = _первая_доступная(_LINUX_ЗАПИСЬ)
        if команда is None:
            raise БуферНедоступен(
                "нет ни wl-clipboard, ни xclip, ни xsel")

    try:
        итог = subprocess.run(команда, input=текст, text=True,
                              capture_output=True, timeout=_ТАЙМАУТ)
    except FileNotFoundError as exc:
        raise БуферНедоступен(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise БуферНедоступен("буфер не ответил") from exc

    if итог.returncode != 0:
        raise БуферНедоступен((итог.stderr or "").strip() or "команда отказала")
