"""Глаза Джарвиса: видеть экран или камеру всё время, а не по одной просьбе.

Раньше Джарвис видел только по команде «посмотри» — один снимок. Теперь
«смотри на экран» / «смотри на меня» включают постоянное зрение: кадры идут
прямо в голосовую сессию Gemini Live (как демонстрация экрана в звонке), и на
«что это за ошибка?», «как тебе?» он отвечает по тому, что видит сейчас.

Бережно к квоте и приватности:
- кадр уходит, только если картинка заметно изменилась (или давно не
  уходил): в разговоре — до раза в секунду, в тишине — раз в несколько секунд;
- 20 минут без разговора — глаза закрываются сами;
- включены глаза — в капсуле значок глаза, включение и выключение — в журнале;
- сам по кадрам Джарвис не заговаривает: отвечает, когда спросили.

«Следи и скажи» (watch): «скажи, когда загрузка дойдёт до 100 %» — раз в 15 с
лёгкая модель смотрит на снимок и отвечает «да/нет»; «да» — Джарвис говорит.
"""
from __future__ import annotations

import io
import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

ACTIVE_INTERVAL = 1.0          # с между кадрами, пока идёт разговор
IDLE_INTERVAL = 4.0            # в тишине
CHANGE = 0.02                  # доля изменения картинки, после которой кадр уходит
KEEPALIVE_ACTIVE = 6.0         # даже без изменений — не реже (в разговоре)
KEEPALIVE_IDLE = 45.0          # и в тишине
AUTO_OFF_SEC = 20 * 60
FRAME_MAX = 1024               # сторона кадра для Live
WATCH_EVERY = 15.0
WATCH_MODEL = "gemini-2.5-flash-lite"
SOURCES = {"screen": "экран", "window": "окно впереди", "camera": "камеру"}


def fingerprint(img) -> bytes:
    """Крошечная серая копия кадра — чтобы понять, изменилось ли что-то."""
    return img.convert("L").resize((48, 27)).tobytes()


def changed(a: bytes | None, b: bytes) -> float:
    """Доля изменения (0..1) между двумя отпечатками."""
    if not a or len(a) != len(b):
        return 1.0
    return sum(abs(x - y) for x, y in zip(a, b)) / (255.0 * len(b))


def to_jpeg(img, max_side: int = FRAME_MAX, quality: int = 70) -> bytes:
    from PIL import Image
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        r = max_side / float(max(w, h))
        img = img.resize((int(w * r), int(h * r)), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class Camera:
    """Веб-камера, открытая на всё время зрения: открывать её на каждый кадр
    — полторы секунды прогрева."""

    def __init__(self):
        self.cap = None

    def grab(self):
        import cv2
        from PIL import Image
        if self.cap is None:
            import os
            import sys
            index = int(os.getenv("JARVIS_CAMERA_INDEX", "0") or 0)
            backend = getattr(cv2, "CAP_DSHOW", None) if sys.platform == "win32" else None
            self.cap = cv2.VideoCapture(index, backend) if backend is not None else cv2.VideoCapture(index)
            if not self.cap.isOpened():
                self.cap = None
                raise RuntimeError("камера не открылась (занята или запрещена в настройках Windows)")
        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise RuntimeError("камера не отдаёт кадр")
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None


def _grab_screen(source: str):
    from actions import vision
    rect = vision._foreground_rect()
    if source == "window" and rect:
        return vision._grab(rect)
    return vision._grab(vision._monitor_for(rect))


class Eyes:
    def __init__(self, send: Callable[[bytes], bool] | None = None,
                 active: Callable[[], bool] | None = None,
                 grab: Callable[[str], object] | None = None,
                 now: Callable[[], float] = time.monotonic):
        self.send = send or (lambda jpeg: False)           # кадр → Live-сессия
        self.active = active or (lambda: False)            # идёт ли разговор
        self.say: Callable[[str], None] = lambda text: None
        self.on_change: Callable[[str], None] = lambda source: None
        self.now = now
        self._grab = grab
        self.camera = Camera()
        self.source = "off"
        self.sent = 0
        self._last_fp: bytes | None = None
        self._last_sent = 0.0
        self._last_active = now()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.watch_condition = ""
        self._watch_stop = threading.Event()

    # ── включение ─────────────────────────────────────────────────────────────
    def open(self, source: str = "screen") -> str:
        source = source if source in SOURCES else "screen"
        self.close(quiet=True)
        self.source = source
        self._last_fp, self._last_sent, self._last_active = None, 0.0, self.now()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="eyes")
        self._thread.start()
        self.on_change(source)
        logger.info("Глаза открыты: %s", SOURCES[source])
        return (f"Смотрю на {SOURCES[source]}, сэр. Спрашивайте о том, что видите вы, — вижу то же. "
                "Скажите «закрой глаза», чтобы я перестал.")

    def close(self, quiet: bool = False) -> str:
        was = self.source
        self.source = "off"
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None
        self.camera.close()
        if was != "off" and not quiet:
            self.on_change("off")
            logger.info("Глаза закрыты (кадров отправлено: %d)", self.sent)
        return "Больше не смотрю, сэр." if was != "off" else "Я и так не смотрю, сэр."

    def status(self) -> str:
        parts = [f"Смотрю на {SOURCES[self.source]}." if self.source != "off" else "Глаза закрыты."]
        if self.watch_condition:
            parts.append(f"Слежу, когда: {self.watch_condition}.")
        return " ".join(parts)

    # ── кадры ─────────────────────────────────────────────────────────────────
    def grab(self):
        if self._grab:
            return self._grab(self.source)
        if self.source == "camera":
            return self.camera.grab()
        return _grab_screen(self.source)

    def step(self) -> bool:
        """Один шаг: снять, сравнить, при надобности отправить. True — ушёл кадр."""
        now = self.now()
        talking = bool(self.active())
        if talking:
            self._last_active = now
        elif now - self._last_active > AUTO_OFF_SEC:
            logger.info("Глаза: %d мин без разговора — закрываю", AUTO_OFF_SEC // 60)
            threading.Thread(target=self.close, daemon=True).start()
            return False
        img = self.grab()
        fp = fingerprint(img)
        keepalive = KEEPALIVE_ACTIVE if talking else KEEPALIVE_IDLE
        if changed(self._last_fp, fp) < CHANGE and now - self._last_sent < keepalive:
            return False
        if self.send(to_jpeg(img)):
            self._last_fp, self._last_sent = fp, now
            self.sent += 1
            return True
        return False

    def _run(self):
        errors = 0
        while not self._stop.is_set():
            try:
                self.step()
                errors = 0
            except Exception as exc:
                errors += 1
                logger.warning("Глаза: %s", exc)
                if errors >= 5:
                    self.say(f"[СИСТЕМА: зрение выключилось: {exc}. Скажи пользователю одной фразой.]")
                    threading.Thread(target=self.close, daemon=True).start()
                    return
            self._stop.wait(ACTIVE_INTERVAL if self.active() else IDLE_INTERVAL)

    # ── следить и сказать ─────────────────────────────────────────────────────
    def watch(self, condition: str, minutes: float = 30, source: str = "screen",
              check: Callable[[bytes, str], bool] | None = None, every: float = WATCH_EVERY) -> str:
        condition = (condition or "").strip()
        if not condition:
            return "За чем следить, сэр?"
        self.stop_watch(quiet=True)
        self.watch_condition = condition
        self._watch_stop = stop = threading.Event()
        check = check or ask_yes_no
        src = source if source in SOURCES else "screen"
        grab = self._grab or (lambda s: self.camera.grab() if s == "camera" else _grab_screen(s))

        def run():
            end = self.now() + minutes * 60
            while not stop.wait(every):
                if self.now() > end:
                    self.say(f"[СИСТЕМА: {int(minutes)} минут следил, а «{condition}» так и не "
                             "случилось. Скажи об этом пользователю одной фразой.]")
                    break
                try:
                    if check(to_jpeg(grab(src), 1280, 80), condition):
                        logger.info("Глаза: дождался «%s»", condition)
                        self.say(f"[СИСТЕМА: ты следил за экраном — случилось: «{condition}». "
                                 "Сообщи пользователю одной короткой фразой.]")
                        break
                except Exception as exc:
                    logger.debug("Слежу за «%s»: %s", condition, exc)
            if self._watch_stop is stop:
                self.watch_condition = ""
        threading.Thread(target=run, daemon=True, name="eyes-watch").start()
        return f"Слежу, сэр. Скажу, когда {condition}."

    def stop_watch(self, quiet: bool = False) -> str:
        had = bool(self.watch_condition)
        self._watch_stop.set()
        self.watch_condition = ""
        return "Перестал следить." if had or quiet else "Я ни за чем не слежу."


def ask_yes_no(jpeg: bytes, condition: str) -> bool:
    """Лёгкая модель: выполнено ли условие на снимке."""
    from google.genai import types

    from actions import vision
    key = vision._get_api_key()
    if not key:
        raise RuntimeError("нет ключа Gemini")
    resp = vision._client(key).models.generate_content(
        model=WATCH_MODEL,
        contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                  f"Посмотри на снимок экрана. Выполнено ли условие: «{condition}»? "
                  "Ответь одним словом: ДА или НЕТ."])
    return (resp.text or "").strip().lower().startswith("да")


_eyes: Eyes | None = None


def eyes() -> Eyes:
    global _eyes
    if _eyes is None:
        _eyes = Eyes()
    return _eyes


def eyes_tool(p: dict) -> str:
    p = p or {}
    a = (p.get("action") or "status").strip().lower()
    e = eyes()
    if a == "open":
        return e.open(str(p.get("source") or "screen"))
    if a == "close":
        return e.close()
    if a == "status":
        return e.status()
    if a == "watch":
        return e.watch(str(p.get("condition") or ""), float(p.get("minutes") or 30),
                       str(p.get("source") or "screen"))
    if a == "stop_watch":
        return e.stop_watch()
    return f"Не понял действие «{a}»."
