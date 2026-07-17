"""
Действие: система режимов ДЖАРВИС.

Один tool `set_mode` управляет 4 lifestyle-режимами:
  - study   (учеба)
  - work    (работа: design/code/client)
  - movie   (кино)
  - music   (музыка: energy/calm/focus/power)
  - normal  (обычный режим — сброс)

Архитектурные принципы:
- Не дублируем функционал (volume/media/etc уже есть в computer_control)
- Переиспользуем существующие actions (open_app, browser_control)
- Graceful fallback: если приложение не установлено — открываем веб-версию
- Конфигурация через config/modes.json (консистентно с api_keys.json)
- Состояние режима пишется в memory/current_mode.json для UI
"""

import json
import os
import sys
from pathlib import Path

from actions.open_app import open_app
from actions.browser_control import browser_control

# ─── Пути ─────────────────────────────────────────────────────────────────────
_BASE        = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _BASE / "config" / "modes.json"
_STATE_PATH  = _BASE / "memory" / "current_mode.json"


# ─── Конфиг и состояние ───────────────────────────────────────────────────────
def _load_config() -> dict:
    """Загружает конфиг режимов или возвращает дефолтный."""
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return _DEFAULT_CONFIG


def _save_state(mode: str, preference: str = "") -> None:
    """Сохраняет текущий режим (для UI и контекста)."""
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "preference": preference}, f, ensure_ascii=False)
    except Exception:
        pass


def get_current_mode() -> dict:
    """Возвращает {'mode': 'work', 'preference': 'design'} или {'mode': 'normal'}."""
    try:
        if _STATE_PATH.exists():
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"mode": "normal", "preference": ""}


# ─── Дефолтная конфигурация (если файл потерян) ───────────────────────────────
_DEFAULT_CONFIG = {
    "study": {
        "apps": ["Microsoft Teams"],
        "urls": ["https://chat.openai.com"],
        "university_url": "",
        "study_folder": "",
    },
    "work": {
        "design_apps": ["Figma"],
        "design_fallback_url": "https://www.figma.com",
        "code_apps": ["Visual Studio Code"],
        "code_fallback_url": "https://github.com",
        "client_apps": ["Telegram"],
        "client_urls": ["https://web.telegram.org", "https://mail.google.com"],
    },
    "movie": {
        "video_search_url": "https://vkvideo.ru/search?q={query}",
        "fallback_search_url": "https://www.youtube.com/results?search_query={query}",
    },
    "music": {
        "service_app": "Spotify",
        "fallback_url": "https://open.spotify.com",
        "playlists": {
            "energy": "https://open.spotify.com/playlist/37i9dQZF1DX76Wlfdnj7AP",
            "calm":   "https://open.spotify.com/playlist/37i9dQZF1DX4WYpdgoIcn6",
            "focus":  "https://open.spotify.com/playlist/37i9dQZF1DWZeKCadgRdKQ",
            "power":  "https://open.spotify.com/playlist/37i9dQZF1DX76Wlfdnj7AP",
        },
    },
}


# ─── Внутренние помощники ─────────────────────────────────────────────────────
def _try_open_apps(app_names: list, player=None) -> list:
    """Пытается открыть список приложений. Возвращает названия открытых."""
    opened = []
    for name in app_names:
        if not name:
            continue
        try:
            result = open_app({"app_name": name}, player=player)
            if result and "не удалось" not in result.lower():
                opened.append(name)
        except Exception:
            pass
    return opened


def _open_url(url: str, player=None) -> bool:
    """Открывает URL в браузере. Возвращает True при успехе."""
    if not url:
        return False
    try:
        browser_control({"action": "go_to", "url": url}, player=player)
        return True
    except Exception:
        return False


def _open_folder(path: str, player=None) -> bool:
    """Открывает папку в проводнике. Возвращает True при успехе."""
    if not path:
        return False
    p = Path(path).expanduser()
    if not p.exists():
        return False
    try:
        if sys.platform == "win32":
            os.startfile(str(p))
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", str(p)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(p)])
        if player:
            player.write_log(f"SYS: Открыта папка — {p}")
        return True
    except Exception:
        return False


# ─── Реализация режимов ───────────────────────────────────────────────────────
def _activate_study(cfg: dict, player=None) -> str:
    s = cfg.get("study", {})

    # Проверяем текущий режим - если уже активен, возвращаем приветствие
    current = get_current_mode()
    if current.get("mode") == "study":
        return "Продолжим учёбу, сэр. Чем могу помочь?"

    if player:
        player.write_log("SYS: 🎓 Активация учебного режима...")

    # Открываем приложения
    _try_open_apps(s.get("apps", []), player)

    # Открываем URL'ы
    for url in s.get("urls", []):
        _open_url(url, player)

    # Университетский сайт (опционально)
    uni = s.get("university_url", "").strip()
    if uni:
        _open_url(uni, player)

    # Учебная папка (опционально)
    folder = s.get("study_folder", "").strip()
    folder_opened = False
    if folder:
        folder_opened = _open_folder(folder, player)

    _save_state("study")

    extras = []
    if uni:
        extras.append("сайт университета")
    if folder_opened:
        extras.append("учебная папка")

    extra_str = f" Также открыл: {', '.join(extras)}." if extras else ""
    return f"Учебный режим активирован, сэр.{extra_str} Включить музыку для концентрации?"


def _activate_work(cfg: dict, preference: str, player=None) -> str:
    """preference: design | code | client (или пусто = спросить)"""
    pref = (preference or "").strip().lower()

    # Если пользователь не указал приоритет — задаём вопрос
    if not pref or pref not in ("design", "code", "client", "дизайн", "код", "клиент"):
        return "Что сегодня в приоритете, сэр? Дизайн, код или клиент?"

    # Нормализация русских вариантов
    pref_map = {"дизайн": "design", "код": "code", "клиент": "client"}
    pref = pref_map.get(pref, pref)

    # Проверяем текущий режим - если уже активен, возвращаем приветствие
    current = get_current_mode()
    if current.get("mode") == "work" and current.get("preference") == pref:
        pref_ru = {"design": "дизайн", "code": "код", "client": "клиент"}.get(pref, pref)
        return f"Продолжим работу в режиме {pref_ru}, сэр. Чем могу помочь?"

    w = cfg.get("work", {})

    if player:
        player.write_log(f"SYS: 💼 Рабочий режим — {pref}...")

    if pref == "design":
        opened = _try_open_apps(w.get("design_apps", []), player)
        if not opened:
            _open_url(w.get("design_fallback_url", ""), player)
        _save_state("work", "design")
        return "Дизайн-окружение готово, сэр."

    elif pref == "code":
        opened = _try_open_apps(w.get("code_apps", []), player)
        if not opened:
            _open_url(w.get("code_fallback_url", ""), player)
        _save_state("work", "code")
        return "Код-окружение готово, сэр."

    elif pref == "client":
        _try_open_apps(w.get("client_apps", []), player)
        for url in w.get("client_urls", []):
            _open_url(url, player)
        _save_state("work", "client")
        return "Клиентское пространство готово, сэр."

    return "Не понял приоритет. Скажите: дизайн, код или клиент."


def _activate_movie(cfg: dict, preference: str, player=None) -> str:
    """
    Movie mode делегирует к movie_player для согласованности.
    movie_player умеет всё: запуск, паузу, перемотку, выход.
    """
    title = (preference or "").strip()
    if not title:
        return "Что будем смотреть, сэр? Назовите фильм."

    # Проверяем текущий режим - если уже активен с тем же фильмом, возвращаем приветствие
    current = get_current_mode()
    if current.get("mode") == "movie" and current.get("preference") == title:
        return f"Продолжаем смотреть '{title}', сэр. Чем могу помочь?"

    # Делегируем продвинутому плееру (он также сохраняет kinogo.mu URL)
    from actions.movie_player import movie_player
    result = movie_player({"action": "play", "title": title}, player=player)

    _save_state("movie", title)
    return result


def _activate_music(cfg: dict, preference: str, player=None) -> str:
    """
    Активация музыкального режима по настроению.
    Делегирует к music_player для надёжного запуска Spotify через URI scheme
    и автоматического воспроизведения через media keys.

    preference: energy | calm | focus | power (или русский эквивалент)
    """
    mood = (preference or "").strip().lower()

    # Нормализация русских вариантов
    mood_map = {
        "энергия": "energy", "энергично": "energy", "бодрая": "energy",
        "спокойствие": "calm", "спокойная": "calm", "расслабляющая": "calm",
        "фон": "focus", "фоновая": "focus", "концентрация": "focus",
        "мощно": "power", "мощная": "power", "тяжёлая": "power", "тяжелая": "power",
    }
    mood = mood_map.get(mood, mood)

    # Проверяем текущий режим - если уже активен с тем же настроением, возвращаем приветствие
    current = get_current_mode()
    if current.get("mode") == "music" and current.get("preference") == mood:
        mood_ru = {"energy": "энергичная", "calm": "спокойная",
                   "focus": "фоновая", "power": "мощная"}.get(mood, mood)
        return f"Продолжаем слушать {mood_ru} музыку, сэр. Чем могу помочь?"

    if not mood or mood not in ("energy", "calm", "focus", "power"):
        return "Какое настроение, сэр? Энергия, спокойствие, фон или мощно?"

    mu = cfg.get("music", {})
    playlists = mu.get("playlists", {})
    playlist_url = playlists.get(mood, "")

    # Делегируем к music_player (надёжный запуск через spotify: URI + media keys)
    from actions.music_player import music_player
    if playlist_url:
        result = music_player(
            {"action": "play", "playlist_url": playlist_url},
            player=player,
        )
    else:
        # Если нет URL плейлиста — запускаем по жанру через поиск
        genre_query = {
            "energy": "energetic music",
            "calm":   "chill music",
            "focus":  "lofi focus",
            "power":  "rock workout",
        }.get(mood, "")
        result = music_player(
            {"action": "play", "query": genre_query},
            player=player,
        )

    _save_state("music", mood)

    mood_ru = {"energy": "энергичная", "calm": "спокойная",
               "focus": "фоновая", "power": "мощная"}.get(mood, mood)

    # Если music_player вернул успех — добавляем mood-info к ответу
    if "не удалось" not in result.lower():
        return f"Включаю {mood_ru} музыку, сэр."
    return result


def _deactivate(player=None) -> str:
    """Возврат в обычный режим. Не закрываем приложения — это решает пользователь."""
    current = get_current_mode()
    prev = current.get("mode", "normal")

    _save_state("normal")

    if player:
        player.write_log("SYS: ↩ Возврат в обычный режим")

    if prev != "normal":
        return "Возвращаюсь в стандартный режим, сэр."
    return "Уже в стандартном режиме, сэр."


# ─── Публичная точка входа (вызывается из Live tool) ──────────────────────────
def set_mode(parameters: dict, player=None) -> str:
    """
    Главная точка входа для tool 'set_mode'.

    parameters:
        mode:       study | work | movie | music | normal
        preference: опциональная под-опция
                    - work:  design | code | client
                    - music: energy | calm | focus | power
                    - movie: название фильма
    """
    mode = (parameters.get("mode") or "").strip().lower()
    preference = (parameters.get("preference") or "").strip()

    cfg = _load_config()

    # Нормализация русских вариантов на всякий случай
    mode_map = {
        "учеба": "study", "учёба": "study", "учебный": "study",
        "работа": "work", "рабочий": "work",
        "кино": "movie", "фильм": "movie",
        "музыка": "music", "музыкальный": "music",
        "обычный": "normal", "выход": "normal",
    }
    mode = mode_map.get(mode, mode)

    if mode == "study":
        return _activate_study(cfg, player)
    elif mode == "work":
        return _activate_work(cfg, preference, player)
    elif mode == "movie":
        return _activate_movie(cfg, preference, player)
    elif mode == "music":
        return _activate_music(cfg, preference, player)
    elif mode == "normal":
        return _deactivate(player)
    else:
        return ("Не понял режим, сэр. Доступны: учеба, работа, кино, музыка, обычный.")
