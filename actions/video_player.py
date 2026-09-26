"""Фильмы (VK Видео), ролики и клипы (YouTube) и голосовое управление просмотром.

Всё идёт в окне браузера Джарвиса (core/browser_cdp.py): поиск на самом
сайте, переход на страницу видео, запуск, полный экран — и дальше пауза,
перемотка, время, громкость, mute напрямую через плеер страницы. Каждое
действие проверяется по состоянию плеера, ответ — то, что произошло.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse

from core import browser_cdp as cdp

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_FILM_MIN_SEC = 40 * 60          # фильм — от 40 минут; короче — трейлер или нарезка


# ── время: «1:20:00», «5 минут», «полчаса» → секунды ─────────────────────────

_WORD_NUM = {"одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "пять": 5,
             "десять": 10, "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40}


def parse_seconds(text, default: float | None = None) -> float | None:
    s = str(text or "").strip().lower().replace(",", ".")
    if not s:
        return default
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", s)
    if m:
        h, mi, se = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        return h * 3600 + mi * 60 + se
    if "полчас" in s:
        return 1800
    if "полминут" in s:
        return 30
    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?|[а-я]+)?\s*(час|мин|сек|h|m|s)\w*", s):
        n = float(num) if num and num[0].isdigit() else _WORD_NUM.get(num, 1)
        total += n * {"час": 3600, "h": 3600, "мин": 60, "m": 60}.get(unit, 1)
        found = True
    if found:
        return total
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else default


def fmt_time(sec: float) -> str:
    sec = int(max(0, sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _say_duration(sec: float) -> str:
    sec = int(sec)
    h, m = sec // 3600, (sec % 3600) // 60
    parts = ([f"{h} ч"] if h else []) + ([f"{m} мин"] if m else [])
    return " ".join(parts) or f"{sec} с"


# ── общий запуск: открыть страницу, дождаться плеера, play, полный экран ─────

def _start(url: str, fullscreen: bool = True, wait_sec: float = 15.0) -> dict | None:
    # Панель браузера рядом с шаром отдаёт окно: фильм — на весь экран.
    try:
        from core import browser_panel
        browser_panel.release_for_video()
    except Exception as exc:
        logger.debug("Панель браузера: %s", exc)
    t = cdp.tab()
    if not t:
        return None
    t.navigate(url)
    if not t.wait(f"(v => v && v.readyState >= 1)({cdp._VIDEO})", timeout=wait_sec):
        return None
    cdp.video_js("v.muted = false; if (v.volume < 0.05) v.volume = 1; try { await v.play() } catch (e) {}")
    st = None
    for _ in range(10):                              # до ~2 с: убедиться, что пошло
        time.sleep(0.2)
        st = cdp.video_state(t)
        if st and not st["paused"]:
            break
    if fullscreen:
        cdp.fullscreen(True, t)
    cdp.bring_to_front()
    return cdp.video_state(t) or st


def _played(st: dict | None) -> bool:
    return bool(st) and not st.get("paused", True)


# ── VK Видео ──────────────────────────────────────────────────────────────────

# Ссылки на видео со страницы результатов + длительность из карточки.
_VK_RESULTS_JS = r"""(() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const m = a.href.match(/\/video(-?\d+_\d+)/);
    if (!m || seen.has(m[1])) continue;
    const card = a.closest('[class*="card" i], [class*="item" i], li, article') || a.parentElement;
    const text = ((card && card.innerText) || a.innerText || '').trim();
    // длительность: «2:04:47», даже вплотную к тексту («HD2:04:47»)
    const dur = (text.match(/(?<![\d:])(?:\d{1,2}:)?\d{1,2}:\d{2}(?![\d:])/) || [''])[0];
    const title = (a.getAttribute('title') || a.getAttribute('aria-label') || a.innerText || text)
                  .trim().split('\n')[0];
    seen.add(m[1]);
    out.push({id: m[1], title, dur});
  }
  return out.slice(0, 30);
})()"""


def _pick_vk(results: list[dict], query: str, film: bool) -> dict | None:
    """Лучший результат: похожее название, для фильма — длинное видео."""
    if not results:
        return None
    try:
        from rapidfuzz import fuzz
        sim = lambda t: fuzz.token_set_ratio(query.lower(), (t or "").lower())  # noqa: E731
    except ImportError:
        sim = lambda t: 100 if query.lower() in (t or "").lower() else 50  # noqa: E731

    def score(r):
        dur = parse_seconds(r.get("dur")) or 0
        s = sim(r.get("title", ""))
        if film:
            s += 40 if dur >= _FILM_MIN_SEC else (-30 if dur and dur < 20 * 60 else 0)
            if re.search(r"трейлер|trailer|тизер|обзор|разбор", r.get("title", ""), re.I):
                s -= 50
        return s
    return max(results, key=score)


def vk_find(title: str, film: bool = True) -> str | None:
    """Страница видео на VK: поиск прямо на сайте в окне Джарвиса (там вход
    пользователя и нет капчи), запасной путь — поисковик."""
    t = cdp.tab()
    q = urllib.parse.quote(title)
    if t:
        for search in (f"https://vkvideo.ru/?q={q}", f"https://vk.com/video?q={q}&section=search"):
            try:
                t.navigate(search)
                results = t.wait(_VK_RESULTS_JS, timeout=7)
            except Exception as exc:
                logger.debug("VK поиск: %s", exc)
                results = None
            best = _pick_vk(results or [], title, film)
            if best:
                return f"https://vkvideo.ru/video{best['id']}"
    from core import web_find
    return web_find.film_page(title + (" фильм" if film else ""), "vk")


def play_film(title: str, player=None) -> str:
    title = (title or "").strip()
    if not title:
        return "Какой фильм включить, сэр?"
    if player:
        player.write_log(f"SYS: 🎬 VK Видео — ищу «{title}»")
    if not cdp.ensure_browser():
        return "Не нашёл Chrome или Edge, чтобы открыть VK Видео, сэр."
    url = vk_find(title)
    if not url:
        return f"Не нашёл «{title}» на VK Видео, сэр."
    st = _start(url, fullscreen=True)
    if not st:
        return f"Открыл «{title}» на VK Видео, но плеер не загрузился — возможно, нужен вход в VK в окне Джарвиса."
    if not _played(st):
        return f"Открыл «{title}» на VK Видео, но видео не запустилось — скажите «продолжи»."
    long = f", {_say_duration(st['d'])}" if st.get("d") else ""
    return f"Включил «{title}» на VK Видео, полный экран{long}."


# ── YouTube ───────────────────────────────────────────────────────────────────

def _yt_html(url: str) -> str:
    import requests
    r = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "ru-RU,ru;q=0.9"},
                     cookies={"CONSENT": "YES+"}, timeout=5)
    r.raise_for_status()
    return r.text


def yt_search(query: str) -> str | None:
    """Первое обычное видео в выдаче (без шортсов и каналов) — id."""
    try:
        html = _yt_html("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
        m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html)
        if m:
            return m.group(1)
    except Exception as exc:
        logger.debug("YouTube поиск (HTML): %s", exc)
    t = cdp.tab()                                    # запасной путь — в самом окне
    if not t:
        return None
    t.navigate("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
    href = t.wait("(document.querySelector('ytd-video-renderer a#video-title') || {}).href", timeout=8)
    m = re.search(r"v=([\w-]{11})", href or "")
    return m.group(1) if m else None


def yt_latest(channel: str) -> str | None:
    """Последнее видео канала: находим канал, берём первое на его «Видео»."""
    try:
        html = _yt_html("https://www.youtube.com/results?sp=EgIQAg%253D%253D&search_query="
                        + urllib.parse.quote(channel))
        m = re.search(r'"canonicalBaseUrl":"(/@[^"]+)"', html)
        if m:
            vids = _yt_html(f"https://www.youtube.com{m.group(1)}/videos")
            v = re.search(r'"videoId":"([\w-]{11})"', vids)
            if v:
                return v.group(1)
    except Exception as exc:
        logger.debug("YouTube канал: %s", exc)
    return yt_search(f"{channel} новое видео")


def play_youtube(query: str = "", channel: str = "", player=None) -> str:
    what = (channel or query or "").strip()
    if not what:
        return "Что включить на YouTube, сэр?"
    if player:
        player.write_log(f"SYS: ▶ YouTube — {'последнее у ' + channel if channel else query}")
    if not cdp.ensure_browser():
        return "Не нашёл Chrome или Edge, чтобы открыть YouTube, сэр."
    vid = yt_latest(channel) if channel else yt_search(query)
    if not vid:
        return f"Не нашёл на YouTube «{what}», сэр."
    st = _start(f"https://www.youtube.com/watch?v={vid}", fullscreen=True)
    t = cdp.tab(create=False)
    title = ""
    if t:
        title = (t.eval("document.title") or "").replace(" - YouTube", "").strip()
    name = f"«{title}»" if title else f"«{what}»"
    if not st:
        return f"Открыл {name} на YouTube, но плеер не загрузился."
    if not _played(st):
        return f"Открыл {name} на YouTube — скажите «продолжи», если не пошло."
    return f"Включил на YouTube {name}."


# ── управление тем, что идёт ──────────────────────────────────────────────────

def control(action: str, value=None) -> str:
    """Пауза, продолжить, перемотка, время, громкость, mute, полный экран…"""
    st = cdp.video_state()
    if not st:
        return ""                                     # видео нет — решит вызывающий
    a = (action or "").strip().lower()

    if a in ("pause", "stop"):
        st = cdp.video_js("v.pause()")
        return "Пауза." if st and st["paused"] else "Не получилось поставить на паузу."
    if a in ("resume", "play"):
        st = cdp.video_js("await v.play()")
        return "Продолжаю." if _played(st) else "Не получилось продолжить."
    if a == "toggle":
        st = cdp.video_js("v.paused ? await v.play() : v.pause()")
        return "Пауза." if st and st["paused"] else "Продолжаю."

    if a in ("seek_forward", "seek_back"):
        sec = parse_seconds(value, 10) or 10
        sign = 1 if a == "seek_forward" else -1
        st = cdp.video_js(f"v.currentTime = Math.max(0, Math.min(v.duration || 1e9, v.currentTime + {sign * sec}))")
        return f"{'Вперёд' if sign > 0 else 'Назад'} на {_say_duration(sec)} — {fmt_time(st['t'])}."
    if a == "seek_to":
        sec = parse_seconds(value)
        if sec is None:
            return "На какое время перемотать, сэр?"
        st = cdp.video_js(f"v.currentTime = Math.min(v.duration || 1e9, {sec})")
        return f"Перемотал на {fmt_time(st['t'])}."
    if a == "restart":
        cdp.video_js("v.currentTime = 0; await v.play()")
        return "С начала."

    if a == "time":
        if not st.get("d"):
            return f"Идёт {fmt_time(st['t'])}."
        left = st["d"] - st["t"]
        return (f"Идёт {fmt_time(st['t'])} из {fmt_time(st['d'])}, "
                f"осталось {_say_duration(left)}.")

    if a in ("volume_up", "volume_down", "volume_set"):
        from actions.computer_settings import parse_level
        cur = round(st["vol"] * 100)
        if a == "volume_set":
            new = parse_level(value, 50)
        else:
            step = parse_level(value, 15) or 15
            new = min(100, cur + step) if a == "volume_up" else max(0, cur - step)
        st = cdp.video_js(f"v.muted = false; v.volume = {new / 100}")
        return f"Громкость видео {round(st['vol'] * 100)}%."
    if a == "mute":
        cdp.video_js("v.muted = true")
        return "Звук видео выключен."
    if a == "unmute":
        cdp.video_js("v.muted = false; if (v.volume < 0.05) v.volume = 0.5")
        return "Звук видео включён."

    if a == "fullscreen":
        return "Полный экран." if cdp.fullscreen(True) else "Не получилось развернуть на весь экран."
    if a == "exit_fullscreen":
        cdp.fullscreen(False)
        return "Вышел из полного экрана."
    if a == "speed":
        rate = parse_seconds(value, 1.0) or 1.0
        rate = max(0.25, min(3.0, rate))
        cdp.video_js(f"v.playbackRate = {rate}")
        return f"Скорость ×{rate:g}."
    if a == "next":
        t = cdp.tab(create=False)
        ok = t and t.eval("(b => b ? (b.click(), true) : false)(document.querySelector('.ytp-next-button'))")
        return "Следующее видео." if ok else "Тут нет следующего видео."
    if a in ("close", "exit"):
        cdp.fullscreen(False)
        cdp.video_js("v.pause()")
        t = cdp.tab(create=False)
        if t:
            t.navigate("about:blank")
        return "Закрыл видео."
    return f"Не понял команду для видео: «{action}»."


def video_playing() -> bool:
    st = cdp.video_state() if cdp.running() else None
    return bool(st) and not st.get("paused", True)
