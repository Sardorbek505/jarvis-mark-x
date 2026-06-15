"""
Веб-камера — снимок с камеры ПК.

Позволяет удалённо посмотреть, что происходит рядом с компьютером:
делает один кадр с камеры и сохраняет в JPEG.
"""
import os
import tempfile
import time
from typing import Dict, Any


def camera_snapshot(parameters: Dict[str, Any] = None, player=None) -> str:
    """Сделать снимок с веб-камеры.

    Args:
        parameters: {"index": <номер камеры, по умолчанию 0>}

    Returns:
        "Снимок с камеры: <путь>" при успехе, иначе текст ошибки.
    """
    params = parameters or {}
    try:
        index = int(params.get("index", 0))
    except (TypeError, ValueError):
        index = 0

    try:
        import cv2  # type: ignore
    except ImportError:
        return ("Камера недоступна: не установлен OpenCV. "
                "Установи на ПК: pip install opencv-python")

    cap = None
    try:
        # На Windows CAP_DSHOW заметно быстрее открывает камеру
        backend = getattr(cv2, "CAP_DSHOW", 0) if os.name == "nt" else 0
        cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)

        if not cap or not cap.isOpened():
            return ("Не удалось открыть камеру. Проверь, что она подключена "
                    "и не занята другим приложением (Zoom, Skype и т.п.).")

        # Прогрев — первые кадры часто тёмные/пустые, пока выставляется экспозиция
        frame = None
        for _ in range(8):
            ok, frame = cap.read()
            if ok and frame is not None:
                pass
            time.sleep(0.05)

        ok, frame = cap.read()
        if not ok or frame is None:
            return "Камера открылась, но не удалось получить кадр. Попробуй ещё раз."

        path = os.path.join(tempfile.gettempdir(), f"jarvis_cam_{int(time.time())}.jpg")
        if not cv2.imwrite(path, frame):
            return "Не удалось сохранить снимок с камеры."

        return f"Снимок с камеры: {path}"

    except Exception as e:
        return f"Ошибка камеры: {e}"
    finally:
        if cap is not None:
            cap.release()
