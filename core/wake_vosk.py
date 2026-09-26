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
    # «л» вместо «в»: живой тест дал «Ты меня слышишь, Чарлис?» на «Джарвис».
    # Целиком «…р?ис» здесь, а не в первой ветке, чтобы не ловить «парус».
    r"|(?<![а-яё])(?:дж|д[аэ]|ча)[аеэя]?р[влф]ис"
    r"|(?<![a-z])(?:dj|j|ch)[ae]r?v[iey]",
    re.IGNORECASE)

MODEL_DIRNAME = "vosk-small-ru"
SAMPLE_RATE = 16000
_COOLDOWN_SEC = 1.5          # одно «Джарвис» — одно пробуждение


# ── Имя, выученное на голосе владельца ───────────────────────────────────────
# Слова «Джарвис» в словаре маленькой модели нет, и свободное распознавание
# пишет вместо него что попало («из» — на синтезе в CI, на живом голосе —
# ни одного попадания за час). Калибровка (core/wake_calibrate.py) слушает, как
# Vosk слышит «Джарвис» у ЭТОГО человека, и сохраняет эти слова — они будят
# вдобавок к WAKE_RE при обычном (свободном) распознавании.
#
# Грамматику «варианты + [unk]» пробовали и отказались: в CI на синтезе она
# ловила 10/10 имён, но давала 10 ложных из 10 — модель, которой выбирать
# только «имя или не имя», записывала в «джарвис» и «сегодня очень жарко».
# Свободное распознавание на тех же фразах: 10/10 и ни одного ложного.
ALIASES_FILE = "wake_aliases.json"
# Короткие частые слова: попадись одно из них в варианты — будило бы на
# каждой второй фразе.
_STOP = {"и", "в", "во", "на", "не", "из", "за", "что", "как", "это", "так", "вот", "да", "нет", "он",
         "она", "они", "мы", "вы", "ты", "я", "его", "ее", "их", "там", "тут", "уже", "еще", "все",
         "всё", "ну", "же", "бы", "ли", "по", "от", "до", "для", "при", "про", "но", "а", "то", "с", "со",
         "к", "ко", "у", "о", "об", "при"}


def aliases_path() -> Path:
    env = os.getenv("JARVIS_WAKE_ALIASES", "").strip()
    if env:
        return Path(env)
    try:
        from core.paths import get_data_root
        return Path(get_data_root()) / ALIASES_FILE
    except Exception:
        return Path(__file__).resolve().parent.parent / ALIASES_FILE


def load_aliases() -> list[str]:
    try:
        data = json.loads(aliases_path().read_text(encoding="utf-8"))
        return [norm(a) for a in data.get("aliases", []) if norm(a)]
    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning("wake_aliases.json не читается: %s", exc)
        return []


def save_aliases(aliases: list[str], report: dict | None = None):
    path = aliases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"aliases": aliases, **(report or {})}, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    return " ".join(re.sub(r"[^a-zа-я0-9\s]", " ", t).split())


def _grams(text: str) -> set[str]:
    words = norm(text).split()
    return set(words) | {" ".join(words[i:i + 2]) for i in range(len(words) - 1)}


def learn_aliases(name_texts: list[list[str]], negative_texts: list[str], max_aliases: int = 6) -> list[str]:
    """Как Vosk слышит имя у этого человека.

    name_texts — для каждой записи «Джарвис» всё, что Vosk выдал (итог,
    промежуточные, альтернативы); negative_texts — то же для обычных фраз.
    Берём слова и пары слов, что повторились хотя бы в четверти записей имени
    (минимум в двух) и ни разу не прозвучали в обычных фразах."""
    import math
    from collections import Counter
    neg: set[str] = set()
    for t in negative_texts:
        neg |= _grams(t)
    counts: Counter = Counter()
    for texts in name_texts:
        grams: set[str] = set()
        for t in texts:
            grams |= _grams(t)
        counts.update(grams)
    need = max(2, math.ceil(len(name_texts) * 0.25))
    good = [g for g, c in counts.most_common()
            if c >= need and g not in neg and g not in _STOP
            and len(g.replace(" ", "")) >= 3 and not all(w in _STOP for w in g.split())]
    return good[:max_aliases]


def matches_alias(text: str, aliases: list[str]) -> bool:
    t = f" {norm(text)} "
    return any(f" {a} " in t for a in aliases)


def scan(rec, pcm: bytes, chunk: int = 2048) -> list[str]:
    """Прогнать запись через распознаватель: итоги, промежуточные и
    альтернативы (если включены) — всё, что он «подумал»."""
    out: list[str] = []

    def take(raw: str):
        d = json.loads(raw)
        for key in ("text", "partial"):
            if d.get(key):
                out.append(d[key])
        for alt in d.get("alternatives") or []:
            if alt.get("text"):
                out.append(alt["text"])

    for i in range(0, len(pcm), chunk):
        if rec.AcceptWaveform(pcm[i:i + chunk]):
            take(rec.Result())
        else:
            take(rec.PartialResult())
    take(rec.FinalResult())
    return out


def _free_recognizer(model):
    import vosk
    rec = vosk.KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetMaxAlternatives(5)
    return rec


def names_in(texts: list[str], aliases: list[str]) -> bool:
    return any(has_wake_word(t) or (aliases and matches_alias(t, aliases)) for t in texts)


def calibrate(model, name_pcms: list[bytes], negative_pcms: list[bytes]) -> dict:
    """Выучить варианты имени и сразу проверить их на тех же записях."""
    pad = b"\0" * SAMPLE_RATE              # полсекунды тишины вокруг — как в жизни
    name_texts = [scan(_free_recognizer(model), pad + p + pad) for p in name_pcms]
    neg_texts = [t for p in negative_pcms for t in scan(_free_recognizer(model), pad + p + pad)]
    aliases = learn_aliases(name_texts, neg_texts)
    report = {"aliases": aliases, "names": len(name_pcms), "negatives": len(negative_pcms),
              "heard": [sorted(set(t))[:6] for t in name_texts]}
    # Проверка — так, как будет работать: свободное распознавание, имя по
    # WAKE_RE или по выученным вариантам.
    hits = sum(names_in(scan(_free_recognizer(model), pad + p + pad), aliases) for p in name_pcms)
    false = sum(names_in(scan(_free_recognizer(model), pad + p + pad), aliases) for p in negative_pcms)
    report.update(hits=hits, false=false)
    return report


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
                 recognizer_factory=None, aliases: list[str] | None = None):
        self._on_wake = on_wake
        self._model_dir = model_dir
        self._factory = recognizer_factory      # для тестов: без vosk и модели
        # Выученные на голосе владельца варианты имени (см. calibrate).
        self._aliases = aliases if aliases is not None else ([] if recognizer_factory else load_aliases())
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
        if self._aliases:
            logger.info("Слово «Джарвис»: вдобавок выученные варианты %s", self._aliases)
        # Свободное распознавание + имя по регулярке и выученным вариантам.
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
            named = text and (has_wake_word(text) or (self._aliases and matches_alias(text, self._aliases)))
            if named and time.monotonic() >= self._cooldown_until:
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
