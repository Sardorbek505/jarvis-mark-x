"""
Определение активного окна и контекста для Vision.
Используется для выбора правильного промпта (Figma vs браузер vs код).
"""

from typing import Tuple

# Ключевые слова в заголовке окна → контекст для промпта
CONTEXT_KEYWORDS = {
    "figma":        ["figma"],
    "browser":      ["chrome", "firefox", "edge", "safari", "opera", "yandex", "brave"],
    "code":         ["visual studio code", "vscode", "pycharm", "intellij",
                     "sublime", "atom", "webstorm"],
    "presentation": ["powerpoint", "keynote", "google slides", "presentation",
                     "слайд"],
    "design":       ["photoshop", "illustrator", "sketch", "adobe xd", "canva",
                     "framer", "affinity"],
    "video":        ["premiere", "after effects", "davinci", "final cut",
                     "capcut"],
    "spreadsheet":  ["excel", "google sheets", "numbers", "libreoffice calc"],
}


def detect_active_window() -> Tuple[str, str]:
    """
    Определяет активное окно и его контекст.

    Возвращает: (context, window_title)
    Context: figma | browser | code | presentation | design | video | spreadsheet | unknown
    """
    try:
        import pygetwindow as gw
        win = gw.getActiveWindow()
        if not win:
            return "unknown", ""

        title = win.title or ""
        title_lower = title.lower()

        for context, keywords in CONTEXT_KEYWORDS.items():
            if any(kw in title_lower for kw in keywords):
                return context, title

        return "unknown", title

    except Exception:
        return "unknown", ""
