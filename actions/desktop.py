"""
Действие: рабочий стол — обои и «сверни всё».

ПРО ОБОИ
    Смысл здесь в одной связке: человек бросает картинку в окно Джарвиса и
    говорит «поставь на обои». Путь называть не надо — берётся последний
    брошенный файл, тот же, о котором спрашивают «что на этой картинке».

    Прежние обои читаются у системы ДО замены, поэтому «отмени» возвращает
    именно их, а не догадку. Если прочитать не удалось — отмена не
    регистрируется вовсе и об этом сказано вслух: отмена, ставящая наугад
    выбранную картинку, хуже, чем её отсутствие.

ПРО «СВЕРНИ ВСЁ»
    На Windows и macOS это одно системное сочетание. На Linux общего способа
    нет: в GNOME он делается через wmctrl, которого может и не быть. Тогда
    инструмент так и говорит, вместо того чтобы отправить сочетание в
    пустоту и отчитаться о сделанном.
"""

import logging
import platform
import subprocess
from pathlib import Path

from core.undo import push_undo

_logger = logging.getLogger(__name__)

_OS = platform.system()

_КАРТИНКИ = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".heic", ".tiff"}

_ФОН = "org.gnome.desktop.background"


# ─── Обои: прочитать ─────────────────────────────────────────────────────────

def _текущие_обои() -> str | None:
    """Путь к нынешним обоям или None, если система не сказала."""
    try:
        if _OS == "Windows":
            import ctypes

            буфер = ctypes.create_unicode_buffer(520)
            # SPI_GETDESKWALLPAPER = 0x0073
            ctypes.windll.user32.SystemParametersInfoW(0x0073, len(буфер), буфер, 0)
            return буфер.value or None

        if _OS == "Darwin":
            вывод = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get picture of current desktop'],
                capture_output=True, text=True, timeout=10)
            путь = вывод.stdout.strip()
            return путь or None

        вывод = subprocess.run(
            ["gsettings", "get", _ФОН, "picture-uri"],
            capture_output=True, text=True, timeout=10)
        значение = вывод.stdout.strip().strip("'\"")
        if значение.startswith("file://"):
            return значение[len("file://"):]
        return значение or None
    except Exception as exc:
        _logger.debug("Нынешние обои не прочитаны: %s", exc)
        return None


# ─── Обои: поставить ─────────────────────────────────────────────────────────

def _поставить_обои(путь: Path) -> bool:
    try:
        if _OS == "Windows":
            import ctypes

            # SPI_SETDESKWALLPAPER=20, SPIF_UPDATEINIFILE|SPIF_SENDCHANGE=3
            return bool(ctypes.windll.user32.SystemParametersInfoW(
                20, 0, str(путь), 3))

        if _OS == "Darwin":
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to set picture of every desktop '
                 f'to POSIX file "{путь}"'],
                check=True, capture_output=True, timeout=15)
            return True

        # GNOME 42 и новее держит отдельную картинку для тёмной темы. Поставить
        # одну светлую значит получить прежние обои при переключении темы —
        # то есть «не сработало» с точки зрения человека.
        for ключ in ("picture-uri", "picture-uri-dark"):
            subprocess.run(["gsettings", "set", _ФОН, ключ, f"file://{путь}"],
                           check=True, capture_output=True, timeout=10)
        return True
    except FileNotFoundError:
        _logger.warning("Нечем ставить обои: команда не найдена")
        return False
    except Exception as exc:
        _logger.warning("Обои не поставились: %s", exc)
        return False


def _обои(параметры: dict, player=None) -> str:
    путь_строкой = str(параметры.get("path", "") or "").strip()
    if not путь_строкой and player is not None:
        путь_строкой = str(getattr(player, "current_file", "") or "")
    if not путь_строкой:
        return ("Какую картинку поставить, сэр? Перетащите её в окно "
                "или назовите путь.")

    путь = Path(путь_строкой).expanduser()
    if not путь.is_file():
        return f"Файла нет по пути {путь}, сэр."
    if путь.suffix.lower() not in _КАРТИНКИ:
        return f"{путь.name} — не картинка, сэр. Обоями это не поставить."

    прежние = _текущие_обои()

    if not _поставить_обои(путь.resolve()):
        if _OS not in ("Windows", "Darwin"):
            return ("Сменить обои не вышло, сэр: в этой системе нет gsettings. "
                    "Поставьте картинку через настройки рабочего стола.")
        return "Сменить обои не вышло, сэр — система отказала."

    if player:
        player.write_log(f"SYS: обои → {путь.name}")

    if прежние and прежние != str(путь.resolve()) and Path(прежние).exists():
        def _вернуть(старые=прежние):
            if _поставить_обои(Path(старые)):
                return f"Вернул прежние обои: {Path(старые).name}."
            return "Прежние обои вернуть не вышло."
        push_undo(f"смена обоев на {путь.name}", _вернуть)
        return f"Поставил {путь.name} на рабочий стол, сэр."

    # Отмена, ставящая наугад выбранную картинку, хуже её отсутствия — но
    # молчать об этом нельзя: человек рассчитывает на «отмени».
    return (f"Поставил {путь.name} на рабочий стол, сэр. "
            f"Прежние обои система не назвала — вернуть их я не смогу.")


# ─── Свернуть всё ────────────────────────────────────────────────────────────

def _свернуть(player=None) -> str:
    try:
        if _OS == "Windows":
            from actions.keyboard import send_combo

            # Возврат проверяем: молча не нажатая комбинация выглядит как
            # «Джарвис меня не послушался», и отчёт о сделанном тут — ложь.
            if not send_combo("win+d"):
                return "Свернуть окна не вышло, сэр — сочетание не отправилось."
            if player:
                player.write_log("SYS: свернул всё")
            return "Свернул всё, сэр."

        if _OS == "Darwin":
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to key code 103'],  # F11
                check=True, capture_output=True, timeout=10)
            if player:
                player.write_log("SYS: свернул всё")
            return "Свернул всё, сэр."

        subprocess.run(["wmctrl", "-k", "on"], check=True,
                       capture_output=True, timeout=10)
        if player:
            player.write_log("SYS: свернул всё")
        return "Свернул всё, сэр."
    except FileNotFoundError:
        return ("Свернуть окна нечем, сэр: в этой системе нет wmctrl. "
                "Общего способа у Linux нет.")
    except Exception as exc:
        _logger.warning("Свернуть не вышло: %s", exc)
        return "Свернуть окна не вышло, сэр."


def desktop(parameters: dict, player=None) -> str:
    параметры = parameters or {}
    действие = str(параметры.get("action", "wallpaper")).strip().lower()

    if действие in ("show", "minimize_all", "свернуть", "показать"):
        return _свернуть(player)
    return _обои(параметры, player)


# ─── Объявление для реестра действий ──────────────────────────────────────────
TOOL = {
    "name": "desktop",
    "description": (
        "Рабочий стол: ставит обои (action=wallpaper) и сворачивает все окна "
        "(action=show). "
        "Обои: путь можно не указывать — возьмётся картинка, которую только что "
        "перетащили в окно. Вызывай на «поставь на обои», «смени обои», "
        "«сделай эту картинку фоном». "
        "Сворачивание — на «сверни всё», «покажи рабочий стол». "
        "Разложить файлы рабочего стола по папкам — это files с "
        "action=organize, а не этот инструмент."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "wallpaper (по умолчанию) | show",
            },
            "path": {
                "type": "STRING",
                "description": "Путь к картинке; пусто — последний брошенный в окно файл",
            },
        },
        "required": [],
    },
    "handler": desktop,
}
