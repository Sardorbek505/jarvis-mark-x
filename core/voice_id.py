"""Узнавание голоса владельца — офлайн, на ПК.

Джарвис пишет людям от вашего имени, звонит им и может выключить компьютер.
Такое «да» должно быть ВАШИМ: не гостя, не телевизора. Один раз вы
записываете 5 коротких фраз (окно «Обо мне» → «Ваш голос»); по ним
считается «отпечаток» голоса — и при подтверждении опасной команды Джарвис
сверяет с ним вашу речь за последние секунды (просьба + «да»).

Отпечаток считает одна из двух моделей:
  • WeSpeaker ResNet34 (sherpa-onnx, 26 МБ) — основная: различает и похожие
    мужские голоса;
  • Vosk spk (16 МБ) — запасная, если нет sherpa-onnx: чужой женский голос
    отсекает, а похожие мужские — плохо (CI: 0.61–0.75 у чужих при 0.73–0.85
    у своего).
Голос, записанный одной моделью, другой не сверить — окно попросит перезаписать.

- Не записали голос или нет модели — проверки нет, всё как раньше.
- Набранное с клавиатуры в окне Джарвиса — доверенное (вы за ПК).
- Звук никуда не уходит: отпечаток считается и хранится на ПК
  (voice_id.json — числа, не запись голоса).
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

SPK_DIRNAME = "vosk-spk"
SPK_URL = "https://alphacephei.com/vosk/models/vosk-model-spk-0.4.zip"
WESPEAKER_DIRNAME = "voice-id"
WESPEAKER_FILE = "wespeaker_en_voxceleb_resnet34.onnx"
WESPEAKER_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
                 + WESPEAKER_FILE)
SAMPLE_RATE = 16000
MIN_SPEECH_SEC = 1.0              # короче — отпечаток ненадёжный
DEFAULT_THRESHOLD = 0.5
ENROLL_PHRASES = [
    "Джарвис, включи музыку и сделай погромче",
    "Какая завтра погода в моём городе?",
    "Напомни мне через час позвонить маме",
    "Сегодня я хочу закончить все задачи по учёбе",
    "Открой браузер и найди свежие новости",
]
ENROLL_SEC = 4.0


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def mean(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(len(vectors[0]))]


def _model_dirs(dirname: str, env: str) -> list[Path]:
    """Где искать модель: переменная, рядом с .exe, в папке данных, в проекте."""
    e = os.getenv(env, "").strip()
    cands = [Path(e)] if e else []
    if getattr(sys, "frozen", False):
        cands += [Path(getattr(sys, "_MEIPASS", "")) / "models" / dirname,
                  Path(sys.executable).parent / "models" / dirname]
    try:
        from core.paths import get_data_root
        cands.append(Path(get_data_root()) / "models" / dirname)
    except Exception:
        pass
    cands.append(Path(__file__).resolve().parent.parent / "models" / dirname)
    return cands


def find_spk_dir() -> Path | None:
    for c in _model_dirs(SPK_DIRNAME, "JARVIS_VOSK_SPK"):
        if (c / "final.ext.raw").exists() or (c / "mfcc.conf").exists():
            return c
    return None


def find_wespeaker() -> Path | None:
    for c in _model_dirs(WESPEAKER_DIRNAME, "JARVIS_WESPEAKER_DIR"):
        if (c / WESPEAKER_FILE).exists():
            return c / WESPEAKER_FILE
    return None


def _fetch(url: str, dest, progress: Callable[[float], None] | None) -> None:
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            dest.write(chunk)
            got += len(chunk)
            if progress and total:
                progress(got / total)


def download_wespeaker(progress: Callable[[float], None] | None = None) -> Path:
    """Скачать WeSpeaker (26 МБ) в папку данных."""
    from core.paths import get_data_root
    dest = Path(get_data_root()) / "models" / WESPEAKER_DIRNAME
    dest.mkdir(parents=True, exist_ok=True)
    part = dest / (WESPEAKER_FILE + ".part")
    with open(part, "wb") as f:
        _fetch(WESPEAKER_URL, f, progress)
    part.replace(dest / WESPEAKER_FILE)
    return dest / WESPEAKER_FILE


def download_spk(progress: Callable[[float], None] | None = None) -> Path:
    """Скачать модель отпечатков Vosk (16 МБ) в папку данных."""
    import tempfile

    from core.paths import get_data_root
    dest = Path(get_data_root()) / "models"
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        _fetch(SPK_URL, tmp, progress)
    try:
        with zipfile.ZipFile(tmp.name) as z:
            top = z.namelist()[0].split("/")[0]
            z.extractall(dest)
        target = dest / SPK_DIRNAME
        if target.exists():
            import shutil
            shutil.rmtree(target)
        (dest / top).rename(target)
    finally:
        os.unlink(tmp.name)
    return target


class VoskEmbedder:
    """Отпечаток голоса из PCM 16 кГц: распознаватель Vosk со SpkModel."""

    name = "vosk"
    size_mb = 16
    # Порог = (самая далёкая своя запись − MARGIN), но в пределах [LO, HI].
    # CI: свои длинные фразы 0.77–0.80, своё короткое «да» 0.55, чужой женский 0.13–0.30.
    LO, HI, MARGIN = 0.42, 0.55, 0.3
    SHORT_DISCOUNT = 0.08

    def __init__(self, asr_dir: Path | None = None, spk_dir: Path | None = None):
        self._lock = threading.Lock()
        self._model = self._spk = None
        self._dirs = (asr_dir, spk_dir)

    def _find(self) -> tuple[Path | None, Path | None]:
        asr, spk = self._dirs
        if asr is None:
            from core.wake_vosk import find_model_dir
            asr = find_model_dir()
        return asr, spk or find_spk_dir()

    def available(self) -> bool:
        return all(self._find())

    def _load(self):
        if self._model is None:
            import vosk
            vosk.SetLogLevel(-1)
            asr, spk = self._find()
            if not asr or not spk:
                raise FileNotFoundError("нет модели Vosk (речь или отпечатки голоса)")
            self._model, self._spk = vosk.Model(str(asr)), vosk.SpkModel(str(spk))

    def __call__(self, pcm: bytes) -> list[float] | None:
        with self._lock:
            self._load()
            import vosk
            rec = vosk.KaldiRecognizer(self._model, SAMPLE_RATE)
            rec.SetSpkModel(self._spk)
            for i in range(0, len(pcm), 8000):
                rec.AcceptWaveform(pcm[i:i + 8000])
            res = json.loads(rec.FinalResult() or "{}")
        vec = res.get("spk")
        return [float(x) for x in vec] if vec else None

    @staticmethod
    def download(progress=None):
        return download_spk(progress)


class SherpaEmbedder:
    """Отпечаток голоса моделью WeSpeaker ResNet34 через sherpa-onnx."""

    name = "wespeaker"
    size_mb = 26
    # CI на живых людях (Mini LibriSpeech, 20 дикторов, чужие того же пола):
    # свои — 5 % ниже 0.849, мин. 0.74; чужие — 99 % ниже 0.836, макс. 0.854.
    LO, HI, MARGIN = 0.82, 0.86, 0.08
    SHORT_DISCOUNT = 0.02

    def __init__(self, model: Path | None = None):
        self._lock = threading.Lock()
        self._model = model
        self._ex = None

    @staticmethod
    def installed() -> bool:
        import importlib.util
        return importlib.util.find_spec("sherpa_onnx") is not None

    def available(self) -> bool:
        return self.installed() and bool(self._model or find_wespeaker())

    def _load(self):
        if self._ex is None:
            import sherpa_onnx
            model = self._model or find_wespeaker()
            if not model:
                raise FileNotFoundError("нет модели WeSpeaker")
            cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=1)
            self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)

    def __call__(self, pcm: bytes) -> list[float] | None:
        import numpy as np
        with self._lock:
            self._load()
            st = self._ex.create_stream()
            st.accept_waveform(SAMPLE_RATE, np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
            st.input_finished()
            if not self._ex.is_ready(st):
                return None
            vec = self._ex.compute(st)
        return [float(x) for x in vec] if len(vec) else None

    @staticmethod
    def download(progress=None):
        return download_wespeaker(progress)


def default_embedder():
    """WeSpeaker, если есть sherpa-onnx (модель докачается из окна), иначе Vosk."""
    return SherpaEmbedder() if SherpaEmbedder.installed() else VoskEmbedder()


class VoiceID:
    def __init__(self, path: Path, embed: Callable[[bytes], list[float] | None] | None = None):
        self.path = Path(path)
        self.embed = embed or default_embedder()
        self.profile: dict = {}
        self.load()

    def load(self):
        try:
            self.profile = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.profile = {}

    @property
    def engine(self) -> str:
        return getattr(self.embed, "name", "")

    def enrolled(self) -> bool:
        """Записан — и той же моделью, что сейчас считает отпечатки."""
        return bool(self.profile.get("mean")) and not self.needs_reenroll()

    def needs_reenroll(self) -> bool:
        """Голос записан другой моделью (старый профиль Vosk, а теперь WeSpeaker)."""
        return bool(self.profile.get("mean")) and self.profile.get("engine", "vosk") != (self.engine or "vosk")

    def download(self, progress=None):
        return self.embed.download(progress)

    def available(self) -> bool:
        avail = getattr(self.embed, "available", None)
        return avail() if callable(avail) else True

    @property
    def threshold(self) -> float:
        return float(self.profile.get("threshold", DEFAULT_THRESHOLD))

    def enroll(self, pcms: list[bytes]) -> dict:
        """Записи → отпечаток. Порог — по тому, насколько ваши же записи
        похожи друг на друга (у тихого микрофона — ниже)."""
        vecs = [v for v in (self.embed(p) for p in pcms if len(p) >= MIN_SPEECH_SEC * SAMPLE_RATE * 2) if v]
        if len(vecs) < 3:
            return {"ok": False, "n": len(vecs),
                    "text": "Мало речи в записях — говорите громче и ближе к микрофону, и повторите."}
        m = mean(vecs)
        sims = [cosine(v, m) for v in vecs]
        # Порог — заметно ниже своих записей, но выше чужих; пределы у каждой
        # модели свои (подобраны в CI: scripts/voice_id_check.py).
        lo, hi, margin = (getattr(self.embed, k, d) for k, d in (("LO", 0.42), ("HI", 0.55), ("MARGIN", 0.3)))
        threshold = max(lo, min(hi, min(sims) - margin))
        self.profile = {"version": 3, "engine": self.engine or "vosk", "mean": m, "n": len(vecs), "self_sim": round(sum(sims) / len(sims), 3),
                        "self_min": round(min(sims), 3), "threshold": round(threshold, 3)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.profile), encoding="utf-8")
        logger.info("Голос записан: %d фраз, сходство своих %.2f, порог %.2f", len(vecs),
                    self.profile["self_sim"], threshold)
        return {"ok": True, "n": len(vecs), "threshold": threshold,
                "text": f"Голос записан ({len(vecs)} фраз). Теперь опасное Джарвис выполнит только по вашему «да»."}

    def verify(self, pcm: bytes) -> tuple[bool | None, float]:
        """(ваш ли голос, сходство). None — проверить нечем (не записан,
        нет модели, мало речи)."""
        if not self.enrolled() or len(pcm) < MIN_SPEECH_SEC * SAMPLE_RATE * 2:
            return None, 0.0
        try:
            vec = self.embed(pcm)
        except Exception as exc:
            logger.warning("Проверка голоса: %s", exc)
            return None, 0.0
        if not vec:
            return None, 0.0
        score = cosine(vec, self.profile["mean"])
        return score >= self.threshold_for(len(pcm)), round(score, 3)

    def threshold_for(self, nbytes: int) -> float:
        """На коротких фразах отпечаток слабее — порог чуть мягче (скидка
        модели при 1 с речи, без скидки от 4 с)."""
        sec = nbytes / (SAMPLE_RATE * 2)
        discount = getattr(self.embed, "SHORT_DISCOUNT", 0.08)
        return self.threshold - discount * max(0.0, min(1.0, (4.0 - sec) / 3.0))

    def reset(self):
        self.profile = {}
        try:
            self.path.unlink()
        except OSError:
            pass


_vid: VoiceID | None = None


def voice_id() -> VoiceID:
    global _vid
    if _vid is None:
        env = os.getenv("JARVIS_VOICE_ID", "").strip()
        if env:
            path = Path(env)
        else:
            from core.paths import get_data_root
            path = Path(get_data_root()) / "voice_id.json"
        _vid = VoiceID(path)
    return _vid


# Что без вашего голоса не делается (если голос записан).
def sensitive(name: str, args: dict) -> bool:
    action = str((args or {}).get("action", "")).lower()
    if name == "contacts":
        return action in ("message", "call")
    if name == "computer_control":
        return any(k in action for k in ("shutdown", "restart", "reboot", "выключ", "перезагруз"))
    if name == "files":
        return any(k in action for k in ("delete", "remove", "удал"))
    if name == "macro":
        return action == "run"
    return False
