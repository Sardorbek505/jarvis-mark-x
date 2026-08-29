"""ДЖАРВИС — Модуль компьютерного зрения и анализа экрана (Vision Mode).

Позволяет Джарвису в реальном времени захватывать экран, активное окно или веб-камеру
и отвечать на любые вопросы пользователя через мультимодальную модель Gemini 2.5.
"""
import io
import logging
import os
import sys
from typing import Optional

from core.paths import load_api_keys

logger = logging.getLogger("jarvis-vision")


def _get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key
    return load_api_keys().get("gemini_api_key", "").strip()


# ── Захват экрана ─────────────────────────────────────────────────────────────
def capture_screen_jpeg(max_size: int = 1280, quality: int = 80) -> bytes | None:
    """Делает снимок основного экрана, масштабирует и возвращает JPEG-байты."""
    # 1. Сначала пробуем нативный захват через PyQt6 (наиболее стабильно на Windows)
    try:
        from PyQt6.QtCore import QBuffer, QIODevice, Qt
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication(sys.argv)
        del _app
        screen = QGuiApplication.primaryScreen()
        if screen:
            pix = screen.grabWindow(0)
            if not pix.isNull():
                if max(pix.width(), pix.height()) > max_size:
                    pix = pix.scaled(
                        max_size,
                        max_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                pix.save(buf, "JPEG", quality)
                return bytes(buf.data())
    except Exception as e:
        logger.debug("PyQt6 screen grab error: %s", e)

    # 2. Фолбэк на PIL ImageGrab / mss
    try:
        from PIL import Image, ImageGrab

        try:
            img = ImageGrab.grab()
        except Exception:
            import mss
            mss_cls = getattr(mss, "MSS", mss.mss)
            with mss_cls() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        if img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > max_size:
            ratio = max_size / float(max(w, h))
            new_size = (int(w * ratio), int(h * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception as e:
        logger.error("Ошибка захвата экрана: %s", e)
        return None


def capture_active_window_jpeg(max_size: int = 1280, quality: int = 80) -> bytes | None:
    """Делает снимок только активного окна пользователя."""
    try:
        if sys.platform == "win32":
            try:
                import pygetwindow as gw
                win = gw.getActiveWindow()
                if win and win.width > 50 and win.height > 50:
                    from PyQt6.QtCore import QBuffer, QIODevice, Qt
                    from PyQt6.QtGui import QGuiApplication
                    from PyQt6.QtWidgets import QApplication

                    _app = QApplication.instance() or QApplication(sys.argv)
                    del _app
                    screen = QGuiApplication.primaryScreen()
                    if screen:
                        pix = screen.grabWindow(0, win.left, win.top, win.width, win.height)
                        if not pix.isNull():
                            if max(pix.width(), pix.height()) > max_size:
                                pix = pix.scaled(
                                    max_size,
                                    max_size,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation,
                                )
                            buf = QBuffer()
                            buf.open(QIODevice.OpenModeFlag.WriteOnly)
                            pix.save(buf, "JPEG", quality)
                            return bytes(buf.data())
            except Exception as e:
                logger.debug("Active window grab via pygetwindow failed: %s", e)
    except Exception:
        pass

    # Фолбэк на общий экран
    return capture_screen_jpeg(max_size, quality)


def capture_camera_jpeg(quality: int = 80) -> bytes | None:
    """Делает один снимок с подключенной веб-камеры."""
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            logger.warning("Веб-камера (индекс 0) не открылась")
            return None

        # Прогрев камеры (первые пару кадров могут быть темными)
        for _ in range(3):
            cap.read()
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return None

        # Конвертация BGR -> JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        ret_enc, buf = cv2.imencode(".jpg", frame, encode_param)
        if ret_enc:
            return buf.tobytes()
        return None
    except Exception as e:
        logger.error("Ошибка захвата камеры: %s", e)
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
        if source == "camera":
            image_bytes = capture_camera_jpeg()
            if not image_bytes:
                return "Сэр, не удалось получить изображение с веб-камеры. Проверьте подключение камеры."
        elif source == "window":
            image_bytes = capture_active_window_jpeg()
            if not image_bytes:
                return "Сэр, не удалось сделать снимок активного окна."
        else:
            image_bytes = capture_screen_jpeg()
            if not image_bytes:
                return "Сэр, не удалось сделать снимок экрана."

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)

        system_instruction = (
            "Ты — Джарвис, высокоинтеллектуальный ИИ-ассистент Тони Старка. "
            "Тебе передан снимок экрана пользователя или камеры. "
            "Отвечай кратко, по делу, естественным русским языком. "
            "Формулируй ответ так, чтобы его было удобно произнести голосом (без сложных таблиц и громоздких markdown конструкций)."
        )

        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

        user_query = prompt if prompt.strip() else "Опиши кратко, что изображено на экране и на что стоит обратить внимание."

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[image_part, user_query],
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.4,
            ),
        )

        if response and response.text:
            return response.text.strip()
        return "Сэр, я проанализировал изображение, но не смог выделить деталей по вашему запросу."
    except Exception as e:
        logger.error("Vision analysis failed: %s", e)
        return f"Сэр, произошла ошибка при зрительном анализе: {e}"


# ── Точка входа для инструментов (Tool Call) ──────────────────────────────────
def vision_action(params: dict) -> str:
    """Функция-обработчик для вызова из ядра команд или Gemini Live tools."""
    prompt = params.get("prompt") or params.get("query") or params.get("question") or ""
    source = params.get("source", "screen").lower()
    if "камер" in prompt.lower() or "camera" in prompt.lower():
        source = "camera"
    elif "окно" in prompt.lower() or "window" in prompt.lower():
        source = "window"
    return analyze_vision(prompt=prompt, source=source)
