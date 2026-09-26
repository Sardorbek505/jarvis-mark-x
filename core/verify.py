"""Проверка после команды: Джарвис сам смотрит, получилось ли.

Раньше «Открыл Telegram» говорилось по факту вызова: открылось ли окно,
развернулось ли, зашёл ли сайт — никто не смотрел. Теперь после команд,
которые меняют экран, через пару секунд делается снимок, и лёгкая модель
отвечает: сделано ли то, что просили. Не сделано — Джарвис честно говорит и
предлагает иначе; сделано — молчит (галочка в журнале), чтобы не болтать
после каждой команды.

Проверка идёт в фоне: ответ пользователю не ждёт снимка. Где итог и так
проверен по факту (музыка — что реально играет, громкость — число из
системы, фильм — состояние плеера), снимок не тратится.
JARVIS_VERIFY=0 — выключить.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

DELAY_SEC = 2.5                  # дать окну открыться и отрисоваться
MODEL = "gemini-2.5-flash-lite"
REPEAT_SEC = 120                 # та же неудача за это время — больше не пробовать самому

# Что проверять глазами: инструмент → действия (None — любые).
VISUAL: dict[str, set | None] = {
    "open_app": None,
    "browser": None,
    "window_control": {"close", "minimize", "maximize", "activate", "snap_left", "snap_right",
                       "open_explorer", "task_manager", "settings", "show_desktop", "minimize_all"},
    "youtube_player": None,
    "video_control": {"fullscreen", "exit_fullscreen", "close"},
}

# Ответ инструмента уже говорит, что не вышло, — проверять нечего.
_FAIL_RE = re.compile(r"(не удалось|не получилось|не нашёл|не найден|ошибка|не запустил|не открыл|"
                      r"не могу|недоступ|нет связи|отменено|сэр, уточните)", re.I)


def enabled() -> bool:
    return os.getenv("JARVIS_VERIFY", "1").strip() != "0"


def should_verify(name: str, args: dict | None, result) -> bool:
    if not enabled() or name not in VISUAL:
        return False
    actions = VISUAL[name]
    if actions is not None and str((args or {}).get("action", "")).lower() not in actions:
        return False
    return not _FAIL_RE.search(str(result or ""))


def describe(name: str, args: dict | None) -> str:
    """Что просили — словами, для модели-проверяльщика."""
    a = args or {}
    if name == "open_app":
        return f"открыть программу «{a.get('app_name', '')}» (её окно должно быть на экране, впереди)"
    if name == "browser":
        what = a.get("url") or a.get("query") or ""
        return f"открыть в браузере «{what}» (сайт или поисковая выдача должна быть видна)"
    if name == "window_control":
        act, tgt = a.get("action", ""), a.get("target", "")
        words = {"close": "закрыть окно", "minimize": "свернуть окно", "maximize": "развернуть окно на весь экран",
                 "activate": "вывести окно вперёд", "snap_left": "прижать окно к левой половине экрана",
                 "snap_right": "прижать окно к правой половине экрана", "open_explorer": "открыть проводник",
                 "task_manager": "открыть диспетчер задач", "settings": "открыть параметры Windows",
                 "show_desktop": "показать рабочий стол", "minimize_all": "свернуть все окна"}
        return words.get(act, act) + (f" программы «{tgt}»" if tgt else "")
    if name == "youtube_player":
        return f"включить на YouTube «{a.get('query') or a.get('channel') or ''}» (ролик должен идти)"
    if name == "video_control":
        return {"fullscreen": "развернуть видео на весь экран", "exit_fullscreen": "выйти из полного экрана",
                "close": "закрыть видео"}.get(a.get("action", ""), a.get("action", ""))
    return f"{name} {a}"


def parse_verdict(text: str) -> tuple[bool | None, str]:
    """{"ok": true/false, "note": "..."} → (ok, note). Не разобрали — (None, "")."""
    try:
        m = re.search(r"\{.*\}", text or "", re.S)
        d = json.loads(m.group(0) if m else text)
        ok = d.get("ok")
        return (bool(ok) if isinstance(ok, bool) else None), str(d.get("note") or "").strip()
    except Exception:
        low = (text or "").strip().lower()
        if low.startswith(("да", "yes")):
            return True, ""
        if low.startswith(("нет", "no")):
            return False, ""
        return None, ""


def ask_model(jpeg: bytes, task: str, reported: str) -> tuple[bool | None, str]:
    from google.genai import types

    from actions import vision
    key = vision._get_api_key()
    if not key:
        return None, ""
    prompt = (f"Ассистент должен был: {task}. Он ответил пользователю: «{reported}».\n"
              "Посмотри на снимок экрана, сделанный через пару секунд после этого. Видно ли, что это "
              "действительно сделано? Если на экране окно ошибки, пустое окно, не та программа или "
              "ничего не поменялось — это не сделано.\n"
              'Ответь JSON: {"ok": true или false, "note": "коротко по-русски, что видно, если не сделано"}')
    resp = vision._client(key).models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"))
    return parse_verdict(resp.text or "")


class Verifier:
    def __init__(self, capture: Callable[[], bytes | None] | None = None,
                 ask: Callable[[bytes, str, str], tuple[bool | None, str]] | None = None,
                 delay: float = DELAY_SEC):
        self.capture = capture or _capture
        self.ask = ask or ask_model
        self.delay = delay
        self.say: Callable[[str], None] = lambda text: None
        self.log: Callable[[str], None] = lambda text: None
        self._failed: dict[str, float] = {}

    def check(self, name: str, args: dict | None, result) -> tuple[bool | None, str]:
        """Синхронно: подождать, снять, спросить. (ok, note)."""
        time.sleep(self.delay)
        jpeg = self.capture()
        if not jpeg:
            return None, ""
        return self.ask(jpeg, describe(name, args), str(result or ""))

    def after(self, name: str, args: dict | None, result) -> bool:
        """После команды: проверить в фоне. True — проверка запущена."""
        if not should_verify(name, args, result):
            return False
        task = describe(name, args)

        def run():
            try:
                ok, note = self.check(name, args, result)
            except Exception as exc:
                logger.debug("Проверка «%s»: %s", task, exc)
                return
            if ok is False:
                logger.info("Проверка: не вышло — %s (%s)", task, note)
                self.log(f"SYS: ⚠ проверил экран — не вышло: {note or task}")
                # Второй раз подряд то же самое — не гонять по кругу «не вышло →
                # другой способ → не вышло»: пусть решает пользователь.
                now = time.monotonic()
                again = now - self._failed.get(task, -1e9) < REPEAT_SEC
                self._failed[task] = now
                if again:
                    self.say(f"[СИСТЕМА: и повторная попытка «{task}» по экрану не видна. Не пробуй "
                             "снова — скажи пользователю одной фразой и спроси, что видит он.]")
                    return
                self.say(f"[СИСТЕМА: ты проверил экран после команды «{task}» — НЕ получилось"
                         + (f": {note}" if note else "") + ". Честно скажи пользователю одной фразой "
                         "и сам попробуй другой способ (или спроси, как лучше).]")
            elif ok:
                self._failed.pop(task, None)
                logger.info("Проверка: получилось — %s", task)
                self.log(f"SYS: ✓ проверил: {task}")
        threading.Thread(target=run, daemon=True, name="verify").start()
        return True


def _capture() -> bytes | None:
    from actions import vision
    return vision.capture_screen_jpeg(max_size=1280, quality=75)


_verifier: Verifier | None = None


def verifier() -> Verifier:
    global _verifier
    if _verifier is None:
        _verifier = Verifier()
    return _verifier
