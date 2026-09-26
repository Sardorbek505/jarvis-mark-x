"""Слово «Джарвис» — прямо на компьютере, как у Алисы и Siri.

Раньше весь звук с микрофона непрерывно уходил в Gemini, а имя искалось в
расшифровке, которую тот присылал обратно. Отсюда три беды: ложные
срабатывания (телевизор, разговор рядом — модель слышала всё и иногда
отвечала), задержка (имя видно только после облачной расшифровки) и
приватность (в облако уезжала вся комната).

Теперь, пока Джарвис «спит», звук слушает маленький офлайн-распознаватель
Vosk с русской моделью (~45 МБ). В облако ничего не идёт. Услышал имя —
Джарвис просыпается, и в Gemini уходят последние ~2 секунды (там само имя и
начало команды: «Джарвис, открой ютуб» говорят на одном дыхании) и всё, что
дальше, пока длится разговор.

Нет модели или пакета vosk — работаем по-старому (имя в расшифровке Gemini),
с записью в логе: Джарвис не должен глохнуть из-за отсутствия словаря.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Имя в тексте. Расшифровка пишет его по-разному: Джарвис, Жарвис, Джервис,
# Джарвиз, Jarvis — плюс узбекская/казахская латиница и падежи («Джарвису»).
# Vosk, не зная такого слова, иногда слышит «дарвис» / «чарвис» — тоже он.
# У «ж» буква «р» обязательна: без неё ловились «жевать», «жива».
WAKE_RE = re.compile(
    r"(?<![а-яёa-z])(?:дж|дз|ҷ)[аеэя]р?[вф][иеэыіа]"
    r"|(?<![а-яёa-z])ж[аеэя]р[вф][иеэыіа]"
    r"|(?<![а-яё])(?:д[аэ]|ча)рвис"
    r"|(?<![a-z])(?:dj|j|ch)[ae]r?v[iey]",
    re.IGNORECASE)

MODEL_DIRNAME = "vosk-small-ru"
SAMPLE_RATE = 16000
_COOLDOWN_SEC = 1.5          # одно «Джарвис» — одно пробуждение


# Расшифровка Gemini гуляет по письменностям: в живом журнале «Джарвис»
# приехал как «ჯარის» (грузинским), рядом были китайский и тайский. Имя должно
# узнаваться и так, поэтому буквы, которыми оно вообще может быть записано,
# переводим в латиницу — дальше работает обычная латинская ветка WAKE_RE.
_TRANSLIT = str.maketrans({
    "ჯ": "j", "ჩ": "ch", "დ": "d", "ა": "a", "ე": "e", "რ": "r",
    "ვ": "v", "ფ": "f", "ი": "i", "ы": "y", "ს": "s", "ზ": "z",
})


# Чужое письмо теряет и звуки: «ჯარის» — это «jaris», без «в». Поэтому здесь
# «в» необязательна, но гласная после «р» обязательна: иначе просыпались бы на
# «jars». Начало на «j/dj/ch» отсекает «Paris».
_TRANSLIT_RE = re.compile(r"(?<![a-z])(?:dj|ch|j)[ae]r[vf]?[iey]s?", re.IGNORECASE)


def has_wake_word(text: str) -> bool:
    if not text:
        return False
    return bool(WAKE_RE.search(text)
                or _TRANSLIT_RE.search(text.translate(_TRANSLIT)))


def find_model_dir() -> Path | None:
    """Папка модели: JARVIS_VOSK_MODEL, рядом с .exe, в %APPDATA%, в проекте."""
    env = os.getenv("JARVIS_VOSK_MODEL", "").strip()
    candidates = [Path(env)] if env else []
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else None
    if base:
        candidates.append(base / "models" / MODEL_DIRNAME)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "models" / MODEL_DIRNAME)
    try:
        from core.paths import get_data_root
        candidates.append(Path(get_data_root()) / "models" / MODEL_DIRNAME)
    except Exception:
        pass
    candidates.append(Path(__file__).resolve().parent.parent / "models" / MODEL_DIRNAME)
    for c in candidates:
        if (c / "am").exists() or (c / "conf").exists():
            return c
    return None


class LocalWake:
    """Офлайн-детектор имени. Кадры кормятся из аудио-колбэка (feed —
    мгновенно), распознавание идёт в своём потоке: колбэк обязан
    возвращаться за миллисекунды."""

    def __init__(self, on_wake: Callable[[str], None], model_dir: Path | None = None,
                 recognizer_factory=None):
        self._on_wake = on_wake
        self._model_dir = model_dir
        self._factory = recognizer_factory      # для тестов: без vosk и модели
        self._q: queue.Queue = queue.Queue(maxsize=400)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cooldown_until = 0.0
        self.ready = False
        self.last_heard = ""

    # ── запуск ───────────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Загружает модель (около секунды) и запускает поток. False —
        локального слова не будет, пусть работает запасной путь."""
        try:
            rec = self._make_recognizer()
        except Exception as exc:
            logger.warning("Локальное слово «Джарвис» недоступно: %s", exc)
            return False
        self._thread = threading.Thread(target=self._run, args=(rec,), daemon=True,
                                        name="wake-vosk")
        self._thread.start()
        self.ready = True
        logger.info("Слово «Джарвис» слушается локально (Vosk)")
        return True

    def stop(self):
        self._stop.set()
        self.ready = False

    def _make_recognizer(self):
        if self._factory:
            return self._factory()
        model_dir = self._model_dir or find_model_dir()
        if not model_dir:
            raise FileNotFoundError(f"нет модели models/{MODEL_DIRNAME}")
        import vosk
        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_dir))
        # Без грамматики: слова «джарвис» в словаре маленькой модели может не
        # быть, и грамматика молча его выкинула бы. Свободное распознавание +
        # регулярка по тексту ловит и «джарвис», и «жарвис», и «дарвис».
        return vosk.KaldiRecognizer(model, SAMPLE_RATE)

    # ── вход ─────────────────────────────────────────────────────────────────
    def feed(self, pcm: bytes):
        """Из аудио-колбэка: положить кадр и сразу вернуться."""
        if not self.ready:
            return
        try:
            self._q.put_nowait(pcm)
        except queue.Full:
            pass                       # поток не успевает — лучше пропуск, чем затор

    def reset(self):
        """Сбросить накопленное: после пробуждения или своей речи."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._reset_pending = True

    # ── поток распознавания ──────────────────────────────────────────────────
    def _run(self, rec):
        self._reset_pending = False
        while not self._stop.is_set():
            try:
                pcm = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._reset_pending:
                self._reset_pending = False
                try:
                    rec.Reset()
                except Exception:
                    pass
            try:
                if rec.AcceptWaveform(pcm):
                    text = json.loads(rec.Result()).get("text", "")
                else:
                    text = json.loads(rec.PartialResult()).get("partial", "")
            except Exception as exc:
                logger.debug("Vosk: %s", exc)
                continue
            if text:
                self.last_heard = text
            if text and has_wake_word(text) and time.monotonic() >= self._cooldown_until:
                self._cooldown_until = time.monotonic() + _COOLDOWN_SEC
                try:
                    rec.Reset()            # иначе то же имя сработает на каждом кадре
                except Exception:
                    pass
                logger.info("Услышал имя локально: «%s»", text)
                try:
                    self._on_wake(text)
                except Exception as exc:
                    logger.warning("Обработчик пробуждения упал: %s", exc)
