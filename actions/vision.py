"""ДЖАРВИС — Модуль компьютерного зрения и анализа экрана (Vision Mode).

Позволяет Джарвису в реальном времени захватывать экран, активное окно или веб-камеру
и отвечать на любые вопросы пользователя через мультимодальную модель Gemini 2.5.
"""
import io
import logging
import os
import sys
import time
from typing import Optional

from core.paths import load_api_keys

logger = logging.getLogger("jarvis-vision")


def _get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key
    return load_api_keys().get("gemini_api_key", "").strip()


# ── Захват экрана ─────────────────────────────────────────────────────────────
# Снимаем через mss из любого потока. Раньше основным был Qt-снимок, а он
# возможен только из GUI-потока — инструменты же идут в пуле потоков, так что
# «активное окно» всегда молча превращалось в снимок всего экрана.

def _to_jpeg(img, max_size: int, quality: int) -> bytes:
    from PIL import Image
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_size:
        r = max_size / float(max(w, h))
        img = img.resize((int(w * r), int(h * r)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _grab(region: dict):
    import mss
    from PIL import Image
    mss_cls = getattr(mss, "MSS", mss.mss)
    with mss_cls() as sct:
        raw = sct.grab(region)
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _foreground_rect() -> dict | None:
    """Прямоугольник окна впереди (не Джарвиса)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        from core import win_apps
        fg = win_apps.foreground()
        if not fg:
            return None
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(fg.hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 80 or h < 80:
            return None
        return {"left": rect.left, "top": rect.top, "width": w, "height": h}
    except Exception as exc:
        logger.debug("Окно впереди: %s", exc)
        return None


def _monitor_for(rect: dict | None) -> dict:
    """Монитор, на котором окно впереди. Раньше снимался только основной —
    ошибка в коде на втором мониторе оставалась невидимой."""
    import mss
    mss_cls = getattr(mss, "MSS", mss.mss)
    with mss_cls() as sct:
        mons = sct.monitors[1:] or sct.monitors
        if rect:
            cx, cy = rect["left"] + rect["width"] // 2, rect["top"] + rect["height"] // 2
            for m in mons:
                if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                    return dict(m)
        return dict(mons[0])


def capture_screen_jpeg(max_size: int = 1600, quality: int = 82) -> bytes | None:
    """Снимок монитора, на котором сейчас работает пользователь."""
    try:
        return _to_jpeg(_grab(_monitor_for(_foreground_rect())), max_size, quality)
    except Exception as e:
        logger.debug("mss: %s", e)
    try:
        from PIL import ImageGrab
        return _to_jpeg(ImageGrab.grab(all_screens=False), max_size, quality)
    except Exception as e:
        logger.error("Ошибка захвата экрана: %s", e)
        return None


def capture_active_window_jpeg(max_size: int = 2000, quality: int = 85) -> bytes | None:
    """Только окно впереди — крупнее, чтобы читался мелкий текст кода."""
    rect = _foreground_rect()
    if rect:
        try:
            return _to_jpeg(_grab(rect), max_size, quality)
        except Exception as e:
            logger.debug("Снимок окна: %s", e)
    return capture_screen_jpeg(max_size, quality)


_CAMERA_ERROR = ""


def capture_camera_jpeg(quality: int = 82) -> bytes | None:
    """Кадр с веб-камеры. На Windows сначала DirectShow (MSMF открывается
    до 10 секунд), прогрев — пока кадр не перестанет быть чёрным."""
    global _CAMERA_ERROR
    _CAMERA_ERROR = ""
    try:
        import cv2
    except ImportError:
        _CAMERA_ERROR = "не установлен OpenCV"
        return None
    index = int(os.getenv("JARVIS_CAMERA_INDEX", "0") or 0)
    backends = [getattr(cv2, "CAP_DSHOW", None), getattr(cv2, "CAP_MSMF", None), None] \
        if sys.platform == "win32" else [None]
    for backend in backends:
        cap = cv2.VideoCapture(index, backend) if backend is not None else cv2.VideoCapture(index)
        try:
            if not cap or not cap.isOpened():
                continue
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            frame, end = None, time.monotonic() + 1.5
            while time.monotonic() < end:
                ok, f = cap.read()
                if ok and f is not None:
                    frame = f
                    if float(f.mean()) > 15:        # экспозиция выставилась
                        break
                time.sleep(0.05)
            if frame is None:
                continue
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ok:
                return buf.tobytes()
        finally:
            if cap is not None:
                cap.release()
    _CAMERA_ERROR = ("камера не открылась: она занята другой программой (Zoom, Teams) или "
                     "запрещена в Параметры → Конфиденциальность → Камера")
    return None


# ── Анализ изображения через Gemini 2.5 ───────────────────────────────────────
def analyze_vision(
    prompt: str,
    source: str = "screen",
    image_bytes: Optional[bytes] = None,
) -> str:
    """Анализирует изображение с экрана или камеры с помощью мультимодальной Gemini.

    Аргументы:
        prompt: Вопрос пользователя к изображению (например, «Что на экране?», «Найди ошибку в коде»).
        source: 'screen' (весь экран), 'window' (активное окно) или 'camera' (веб-камера).
        image_bytes: Опциональные готовые байты изображения JPEG.
    """
    key = _get_api_key()
    if not key:
        return "Сэр, не найден API-ключ Gemini. Пожалуйста, укажите его в настройках."

    if not image_bytes:
        src = str(source).strip().lower()
        if src in ("camera", "webcam"):
            image_bytes = capture_camera_jpeg()
            if not image_bytes:
                return f"Сэр, не удалось получить кадр с веб-камеры: {_CAMERA_ERROR or 'нет камеры'}."
        elif src in ("window", "active_window", "active"):
            image_bytes = capture_active_window_jpeg()
            if not image_bytes:
                return "Сэр, не удалось сделать снимок активного окна."
        else:
            image_bytes = capture_screen_jpeg()
            if not image_bytes:
                return "Сэр, не удалось сделать снимок экрана."

    try:
        from google.genai import types

        system_instruction = (
            "Ты — Джарвис. Тебе передан снимок экрана пользователя или кадр с камеры. "
            "Отвечай на вопрос по тому, что реально видно, коротко (1–3 фразы), естественным "
            "русским языком, чтобы удобно было произнести. Если просят найти ошибку в коде — "
            "назови файл/строку и суть ошибки. Не видно — так и скажи, не выдумывай."
        )
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        user_query = prompt if prompt.strip() else "Кратко: что на экране и на что стоит обратить внимание?"

        response = _client(key).models.generate_content(
            model=_VISION_MODEL,
            contents=[image_part, user_query],
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,
                # Думание у 2.5-flash тратит тот же лимит токенов: при 300 и
                # включённом думании ответ приходил пустым или обрезанным —
                # «не смог выделить деталей». Думание выключено, лимит шире.
                max_output_tokens=900,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            ),
        )
        if response and response.text:
            return response.text.strip()
        return "Сэр, я посмотрел, но не разобрал деталей — попробуйте спросить точнее."
    except Exception as e:
        logger.error("Vision analysis failed: %s", e)
        return f"Сэр, не получилось разобрать изображение: {e}"


_VISION_MODEL = os.getenv("JARVIS_VISION_MODEL", "gemini-2.5-flash")
_clients: dict = {}


def _client(key: str):
    """Один клиент на ключ, с таймаутом: раньше без него зависшая сеть
    держала весь приём звука до 45 секунд."""
    if key not in _clients:
        from google import genai
        from google.genai import types
        _clients[key] = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=20000))
    return _clients[key]


# ── Точка входа для инструментов (Tool Call) ──────────────────────────────────
def vision_action(params: dict) -> str:
    """Функция-обработчик для вызова из ядра команд или Gemini Live tools."""
    prompt = params.get("prompt") or params.get("query") or params.get("question") or ""
    focus = params.get("focus", "")
    mode = params.get("mode", "")
    source = params.get("source", "screen")

    if mode:
        source = mode
    if focus and focus != "general":
        prompt = (
            f"Проведи экспертный анализ ({focus}): {prompt}"
            if prompt
            else f"Проведи экспертный анализ экрана с фокусом на {focus}."
        )

    src_lower = str(source).strip().lower()
    explicit = bool(params.get("source") or mode)
    if src_lower in ("window", "active_window", "active"):
        source = "active_window"
    elif not explicit:
        # Угадываем по словам только если источник не задан: «что за окном?»
        # с source=camera раньше превращалось в скриншот активного окна.
        if "камер" in prompt.lower() or "camera" in prompt.lower():
            source = "camera"
        elif "окно" in prompt.lower():
            source = "active_window"
    return analyze_vision(prompt=prompt, source=source)
