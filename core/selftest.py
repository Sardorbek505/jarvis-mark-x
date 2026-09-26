"""`JARVIS.exe --selftest` — проверка частей, которые можно проверить без
человека: словарь слова «Джарвис», громкость, список программ.

Зачем: в сборке что-то может не доехать (DLL Vosk, модель, COM-обвязка
pycaw), и узнаём мы об этом от пользователя — «ничего не работает».
Самопроверка гоняется в CI на настоящей Windows сразу после установки.

Каждая проверка — (имя, функция). Функция возвращает строку-итог или
бросает исключение. Обязательные (REQUIRED) валят код возврата.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("jarvis-selftest")


def _check_wake() -> str:
    from core.wake_vosk import LocalWake, find_model_dir
    model = find_model_dir()
    if not model:
        raise RuntimeError("модель vosk-small-ru не найдена")
    w = LocalWake(lambda t: None, model_dir=model)
    t0 = time.monotonic()
    if not w.start():
        raise RuntimeError("Vosk не запустился")
    w.feed(b"\0" * 32000)                 # секунда тишины — распознаватель жив
    time.sleep(0.3)
    w.stop()
    return f"модель {model.name} загружена за {time.monotonic() - t0:.1f} с"


def _check_volume() -> str:
    from actions.computer_settings import get_volume
    v = get_volume()
    if v is None:
        raise RuntimeError("не Windows")
    return f"громкость читается: {v}%"


def _check_brightness() -> str:
    import screen_brightness_control as sbc
    return f"мониторов с яркостью: {len(sbc.list_monitors())}"


def _check_apps() -> str:
    from core import win_apps
    apps = win_apps.build_index(force=True)
    if not apps:
        raise RuntimeError("индекс программ пуст")
    probe = win_apps.find_app("блокнот") or win_apps.find_app("edge")
    return f"программ в индексе: {len(apps)}, проба: {probe.name if probe else 'не нашёл'}"


def _check_media() -> str:
    from core import media_session
    if not media_session.available():
        raise RuntimeError("winrt Media.Control не импортируется")
    np = media_session.now_playing()
    return "медиа-сессии доступны" + (f", играет: {np.title}" if np else ", сейчас тишина")


def _check_search() -> str:
    import ddgs  # noqa: F401
    return "ddgs импортируется"


def _check_browser() -> str:
    from websockets.sync.client import connect  # noqa: F401 — управление видео через CDP
    from core import browser_cdp
    exe = browser_cdp.browser_exe()
    if not exe:
        raise RuntimeError("нет Chrome и Edge — фильмы и YouTube не запустить")
    return f"браузер для видео: {exe}"


def _check_calls() -> str:
    import ntgcalls  # noqa: F401 — нативная библиотека звонков (DLL) доехала
    import pytgcalls
    import telethon
    from core import tg_call
    return f"звонки: py-tgcalls {pytgcalls.__version__}, telethon {telethon.__version__}; " + \
        (tg_call.ready() or "аккаунт подключён")


CHECKS = [("wake", _check_wake), ("media", _check_media), ("search", _check_search), ("volume", _check_volume), ("brightness", _check_brightness),
          ("apps", _check_apps), ("browser", _check_browser),
          ("calls", _check_calls)]
REQUIRED = {"wake", "apps", "media", "search", "browser", "calls"}


def register(name: str, fn, required: bool = False):
    CHECKS.append((name, fn))
    if required:
        REQUIRED.add(name)


def run() -> int:
    """Прогнать проверки, записать итоги в лог. 0 — обязательные прошли."""
    failed = []
    for name, fn in CHECKS:
        try:
            logger.info("SELFTEST %s: OK — %s", name, fn())
        except Exception as exc:
            logger.error("SELFTEST %s: FAIL — %s", name, exc)
            if name in REQUIRED:
                failed.append(name)
    logger.info("SELFTEST итог: %s", "OK" if not failed else "FAIL " + ",".join(failed))
    return 1 if failed else 0
