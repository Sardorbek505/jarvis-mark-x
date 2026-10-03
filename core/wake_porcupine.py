"""Слово «Джарвис» офлайн — Porcupine (Picovoice) со своим словом владельца.

Почему не Vosk: в маленькой русской модели Vosk слова «джарвис» нет вовсе —
она слышит «из», и детектор срабатывал лишь по случайным догадкам. Porcupine
обучает модель именно на этом слове (консоль Picovoice, бесплатно для личного
пользования): владелец набирает «Джарвис», выбирает русский язык и скачивает
файл слова (.ppn). Для русского нужен ещё файл языковой модели (.pv) — его
кладут рядом, Джарвис найдёт сам.

Где искать (первое найденное):
  ключ   — PICOVOICE_ACCESS_KEY или picovoice_access_key в «Ключах»;
  слово  — PORCUPINE_KEYWORD / porcupine_keyword, иначе первый *.ppn в
           <папка данных>/wake/;
  модель — PORCUPINE_MODEL / porcupine_model, иначе *.pv рядом со словом.

Интерфейс — как у core/wake_vosk.LocalWake: start() → bool, feed(pcm 16 кГц
int16), stop(); при срабатывании зовёт on_wake(текст).
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

COOLDOWN_SEC = 1.5            # одно «Джарвис» — одно срабатывание
SENSITIVITY = float(os.getenv("PORCUPINE_SENSITIVITY", "0.6"))


def _keys() -> dict:
    try:
        from core.paths import load_api_keys
        return load_api_keys()
    except Exception:
        return {}


def _wake_dir() -> Path | None:
    try:
        from core.paths import get_data_root
        return Path(get_data_root()) / "wake"
    except Exception:
        return None


def find_files(keys: dict | None = None) -> tuple[str, str, str]:
    """(ключ, путь к .ppn, путь к .pv или "")."""
    k = keys if keys is not None else _keys()
    access = (os.getenv("PICOVOICE_ACCESS_KEY") or k.get("picovoice_access_key") or "").strip()
    kw = (os.getenv("PORCUPINE_KEYWORD") or k.get("porcupine_keyword") or "").strip().strip('"')
    model = (os.getenv("PORCUPINE_MODEL") or k.get("porcupine_model") or "").strip().strip('"')
    if not kw:
        d = _wake_dir()
        if d and d.is_dir():
            ppn = sorted(d.glob("*.ppn"))
            kw = str(ppn[0]) if ppn else ""
    if kw and not model:
        pv = sorted(Path(kw).parent.glob("*.pv"))
        model = str(pv[0]) if pv else ""
    return access, kw, model


def problem(keys: dict | None = None) -> str:
    """Что мешает включить детектор ("" — ничего)."""
    access, kw, model = find_files(keys)
    if not access:
        return "нет ключа Picovoice (AccessKey)"
    if not kw:
        return "нет файла слова «Джарвис» (.ppn)"
    if not Path(kw).is_file():
        return f"файл слова не найден: {Path(kw).name}"
    if model and not Path(model).is_file():
        return f"файл модели не найден: {Path(model).name}"
    if "_ru_" in Path(kw).name.lower() and not model:
        return "для русского слова нужен файл модели porcupine_params_ru.pv рядом с .ppn"
    return ""


def configured() -> bool:
    return not problem()


def create_engine(access: str, kw: str, model: str = ""):
    import pvporcupine
    kwargs = {"access_key": access, "keyword_paths": [kw], "sensitivities": [SENSITIVITY]}
    if model:
        kwargs["model_path"] = model
    return pvporcupine.create(**kwargs)


class PorcupineWake:
    def __init__(self, on_wake: Callable[[str], None], engine_factory: Callable | None = None):
        self._on_wake = on_wake
        self._factory = engine_factory          # для тестов: без pvporcupine и ключа
        self._q: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._cooldown_until = 0.0
        self._engine = None
        self.ready = False
        self.last_heard = ""

    def start(self) -> bool:
        try:
            if self._factory is not None:
                self._engine = self._factory()
            else:
                why = problem()
                if why:
                    logger.info("Porcupine не включён: %s", why)
                    return False
                self._engine = create_engine(*find_files())
        except Exception as exc:
            # Ключ не тот, .ppn от другой версии, нет библиотеки — назад к Gemini.
            logger.warning("Porcupine не запустился: %s", exc)
            return False
        threading.Thread(target=self._run, daemon=True, name="wake-porcupine").start()
        self.ready = True
        logger.info("Слово «Джарвис» слушает Porcupine (чувствительность %.2f)", SENSITIVITY)
        return True

    def stop(self):
        self._stop.set()
        self.ready = False

    def feed(self, pcm: bytes):
        if not self.ready:
            return
        try:
            self._q.put_nowait(pcm)
        except queue.Full:
            pass                                 # отстаём — старый звук не ждём

    def _run(self):
        frame = int(getattr(self._engine, "frame_length", 512))
        buf = np.zeros(0, dtype=np.int16)
        try:
            while not self._stop.is_set():
                try:
                    pcm = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                buf = np.concatenate([buf, np.frombuffer(pcm, dtype=np.int16)])
                while len(buf) >= frame:
                    chunk, buf = buf[:frame], buf[frame:]
                    if self._engine.process(chunk.tolist()) >= 0 and time.monotonic() >= self._cooldown_until:
                        self._cooldown_until = time.monotonic() + COOLDOWN_SEC
                        self.last_heard = "джарвис"
                        try:
                            self._on_wake("джарвис")
                        except Exception as exc:
                            logger.warning("Обработчик слова: %s", exc)
        finally:
            delete = getattr(self._engine, "delete", None)
            if delete:
                try:
                    delete()
                except Exception:
                    pass
