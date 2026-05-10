"""
ДЖАРВИС — Модуль Vision (анализ экрана)
"""
from vision.screen_capture import (
    capture_active_window,
    capture_full_screen,
    compress_image,
)
from vision.window_detector import detect_active_window
from vision.vision_analyzer import analyze_image

__all__ = [
    "capture_active_window",
    "capture_full_screen",
    "compress_image",
    "detect_active_window",
    "analyze_image",
]
