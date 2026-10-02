"""Настройки Джарвиса: микрофон, динамик, чувствительность, голос, обращение, камера…

Раньше всё это задавалось переменными окружения (MIC_DEVICE, MIC_RMS_THRESHOLD,
JARVIS_WAKE_MODE…) — человеку из exe недоступно. Теперь — экран «Настройки»
(ui_settings.py) и файл settings.json в папке данных.

Как применяется:
  • при старте apply_env() кладёт сохранённое в os.environ — старый код,
    читающий переменные, работает как раньше;
  • в файле — только то, что человек менял: не тронутое остаётся значением по
    умолчанию (или тем, что задано в .env);
  • set() сохраняет, обновляет os.environ и зовёт подписчиков — main.py
    переоткрывает микрофон/динамик и меняет порог без перезапуска.

Устройства храним по ИМЕНИ, а не номеру: номера в Windows меняются после
перезагрузки и подключения наушников.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Opt:
    key: str
    env: str | None          # переменная окружения, которую читает код
    default: Any
    restart: bool = False    # применится только после перезапуска


OPTS = [
    Opt("mic", "MIC_DEVICE", ""),                       # имя микрофона; "" — автовыбор
    Opt("speaker", "JARVIS_OUTPUT_DEVICE", ""),         # имя динамика; "" — системный
    Opt("mic_threshold", "MIC_RMS_THRESHOLD", 150),     # тише — не считается речью
    Opt("ignore_speakers", "MIC_IGNORE_SPEAKERS", True),  # не слушать, пока играет музыка/фильм
    Opt("wake_mode", "JARVIS_WAKE_MODE", "wake_word"),  # wake_word | always_on
    Opt("awake_sec", "JARVIS_AWAKE_SEC", 30),           # сколько слушать без имени после ответа
    Opt("voice", None, ""),                             # fish | gemini (в api_keys: jarvis_voice)
    Opt("edge_voice", "EDGE_VOICE", "ru-RU-DmitryNeural"),
    Opt("camera", "JARVIS_CAMERA_INDEX", 0),
    Opt("briefing", "JARVIS_BRIEFING", True),
    Opt("island", "JARVIS_ISLAND", True, restart=True),
    Opt("animations", "JARVIS_ANIMATIONS", True),       # переходы, подсветка, волны (ui_anim)
    Opt("quick_voice", "JARVIS_QUICK_VOICE", False),    # простые команды — голосом, а не звуком
]
BY_KEY = {o.key: o for o in OPTS}

_lock = threading.RLock()
_subs: list[Callable[[str, Any], None]] = []


def path() -> Path:
    env = os.getenv("JARVIS_SETTINGS", "").strip()
    if env:
        return Path(env)
    from core.paths import get_data_root
    return Path(get_data_root()) / "config" / "settings.json"


def _saved() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if k in BY_KEY} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _env_value(o: Opt) -> Any:
    """Что сейчас в окружении (задано в .env или раньше этим модулем)."""
    raw = os.getenv(o.env or "", "")
    if not o.env or raw == "":
        return None
    if isinstance(o.default, bool):
        return raw.strip() not in ("0", "false", "False", "")
    if isinstance(o.default, int):
        try:
            return int(float(raw))
        except ValueError:
            return None
    return raw


def load() -> dict:
    """Все настройки: сохранённое → окружение → по умолчанию."""
    saved = _saved()
    out = {}
    for o in OPTS:
        if o.key in saved:
            out[o.key] = saved[o.key]
        elif o.key == "voice":
            out[o.key] = _voice_from_keys()
        else:
            env = _env_value(o)
            out[o.key] = o.default if env is None else env
    return out


def get(key: str) -> Any:
    return load()[key]


def _env_str(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def apply_env(values: dict | None = None) -> None:
    """Сохранённое → os.environ (при старте, до чтения переменных)."""
    for k, v in (values if values is not None else _saved()).items():
        o = BY_KEY.get(k)
        if o and o.env:
            if v in ("", None):
                os.environ.pop(o.env, None)
            else:
                os.environ[o.env] = _env_str(v)


def set(key: str, value: Any) -> None:  # noqa: A001 — settings.set читается естественно
    o = BY_KEY[key]
    if isinstance(o.default, bool):
        value = bool(value)
    elif isinstance(o.default, int):
        value = int(value)
    else:
        value = "" if value is None else str(value)
    with _lock:
        data = _saved()
        data[key] = value
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    if key == "voice":
        _save_voice(value)
    apply_env({key: value})
    for fn in list(_subs):
        try:
            fn(key, value)
        except Exception as exc:
            logger.warning("Настройка %s: подписчик упал: %s", key, exc)


def subscribe(fn: Callable[[str, Any], None]) -> None:
    _subs.append(fn)


def unsubscribe(fn) -> None:
    if fn in _subs:
        _subs.remove(fn)


# ── голос — там же, где его всегда читал main.py (api_keys: jarvis_voice) ────
def _voice_from_keys() -> str:
    """Как выбирает main.py: сохранённый голос, иначе Fish при ключе, иначе Gemini."""
    try:
        # Читаем тем же путём, которым пишем (_save_voice): core.keys.load_values
        # поля jarvis_voice не знает — экран показывал не тот голос.
        from core.paths import load_api_keys
        saved = str(load_api_keys().get("jarvis_voice") or "").strip().lower()
        if saved:
            return saved
    except Exception as exc:
        logger.debug("Голос из ключей не прочитался: %s", exc)
    try:
        from telegram_bot import tts_fish
        return "fish" if tts_fish.is_configured() else "gemini"
    except Exception:
        return "gemini"


def _save_voice(value: str) -> None:
    try:
        from core.paths import save_api_keys
        save_api_keys({"jarvis_voice": value})
    except Exception as exc:
        logger.warning("Голос не сохранился в ключи: %s", exc)


# ── устройства ───────────────────────────────────────────────────────────────
def devices(kind: str) -> list[str]:
    """Имена микрофонов (kind="input") или динамиков ("output") без повторов.
    Берём MME (hostapi 0) — те же имена, что в «Звук» Windows."""
    try:
        import sounddevice as sd
        items = sd.query_devices()
    except Exception as exc:
        logger.debug("Устройства звука: %s", exc)
        return []
    ch = "max_input_channels" if kind == "input" else "max_output_channels"
    names: list[str] = []
    for d in items:
        if d.get(ch, 0) <= 0 or d.get("hostapi", 0) != 0:
            continue
        name = str(d["name"]).strip()
        low = name.lower()
        if name in names or "mapper" in low or "переназначение" in low or "первичный" in low:
            continue
        names.append(name)
    return names


def find_device(name: str, kind: str, query=None) -> int | None:
    """Номер устройства по имени (точно, потом по началу — MME режет имя до 31 символа)."""
    if not name:
        return None
    try:
        items = query() if query else __import__("sounddevice").query_devices()
    except Exception:
        return None
    ch = "max_input_channels" if kind == "input" else "max_output_channels"
    cands = [(i, str(d["name"])) for i, d in enumerate(items) if d.get(ch, 0) > 0]
    for i, n in cands:
        if n == name:
            return i
    low = name.lower()
    for i, n in cands:
        if n.lower().startswith(low[:28]) or low.startswith(n.lower()[:28]):
            return i
    return None
