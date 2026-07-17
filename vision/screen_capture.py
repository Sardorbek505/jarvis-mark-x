"""
Захват экрана для модуля Vision.
Поддерживает: активное окно, полный экран, сжатие в JPEG.
"""

import io
from typing import Optional

from PIL import Image

import logging

_logger = logging.getLogger(__name__)

# Опционально: mss для быстрого захвата
try:
    import mss
    _HAS_MSS = True
except ImportError:
    _HAS_MSS = False

# Параметры сжатия (экономят токены при отправке в Vision API)
MAX_DIM = 1280       # Максимальный размер по большей стороне
JPEG_QUALITY = 75    # Качество JPEG (75 — хороший баланс качество/размер)


def capture_full_screen() -> Optional[Image.Image]:
    """Захват всего основного монитора."""
    if _HAS_MSS:
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # Основной монитор
                shot = sct.grab(monitor)
                return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    # Fallback на PIL ImageGrab
    try:
        from PIL import ImageGrab
        return ImageGrab.grab()
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
        return None


def capture_active_window() -> Optional[Image.Image]:
    """
    Захват только активного окна.
    Если не получилось — возвращает полный экран.
    """
    try:
        import pygetwindow as gw
        win = gw.getActiveWindow()
        if win and win.width > 50 and win.height > 50:
            # Координаты окна
            left, top = win.left, win.top
            width, height = win.width, win.height

            if _HAS_MSS:
                try:
                    with mss.mss() as sct:
                        region = {
                            "left": left, "top": top,
                            "width": width, "height": height,
                        }
                        shot = sct.grab(region)
                        return Image.frombytes(
                            "RGB", shot.size, shot.bgra, "raw", "BGRX"
                        )
                except Exception as exc:
                    _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

            # Fallback PIL
            from PIL import ImageGrab
            bbox = (left, top, left + width, top + height)
            return ImageGrab.grab(bbox=bbox)
    except Exception as exc:
        _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

    # Если активное окно не определилось — полный экран
    return capture_full_screen()


def compress_image(img: Image.Image, max_dim: int = MAX_DIM) -> bytes:
    """
    Сжатие изображения в JPEG для отправки в Vision API.
    Ресайз до max_dim по большей стороне + JPEG quality 75.
    Экономит ~80% токенов по сравнению с PNG 4K.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        new_size = (int(w * ratio), int(h * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()
