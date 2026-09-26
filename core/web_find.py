"""Поиск в интернете без ключей: для музыки (ссылка на трек Spotify), фильмов
(страница на VK Видео / Кинопоиске) и фактов.

Основной путь — пакет ddgs (DuckDuckGo). Запасной — HTML-версия DuckDuckGo
напрямую. Раньше поиск шёл через DuckDuckGo Instant Answer API: на русские
запросы он почти всегда пуст, и Джарвис молча открывал вкладку браузера.
"""
from __future__ import annotations

import html
import logging
import re
import urllib.parse
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


@dataclass
class Hit:
    title: str
    url: str
    snippet: str = ""


def search(query: str, limit: int = 8, region: str = "ru-ru") -> list[Hit]:
    try:
        from ddgs import DDGS
        with DDGS(timeout=8) as d:
            rows = d.text(query, region=region, max_results=limit) or []
        hits = [Hit(r.get("title", ""), r.get("href", ""), r.get("body", "")) for r in rows]
        if hits:
            return hits
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("ddgs: %s", exc)
    try:
        return _html_search(query, limit, region)
    except Exception as exc:
        logger.debug("DuckDuckGo HTML: %s", exc)
        return []


def _html_search(query: str, limit: int, region: str) -> list[Hit]:
    import requests
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": query, "kl": region},
                      headers={"User-Agent": _UA}, timeout=8)
    r.raise_for_status()
    hits = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                         r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S):
        url = m.group(1)
        if "uddg=" in url:                                   # ссылки-переходы DDG
            url = urllib.parse.unquote(re.search(r"uddg=([^&]+)", url).group(1))
        clean = lambda s: html.unescape(re.sub(r"<.*?>", "", s)).strip()  # noqa: E731
        hits.append(Hit(clean(m.group(2)), url, clean(m.group(3))))
        if len(hits) >= limit:
            break
    return hits


# ── Spotify ───────────────────────────────────────────────────────────────────

_SPOTIFY_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist|artist)/([A-Za-z0-9]{22})")


def spotify_uri(query: str, kind: str | None = None) -> str | None:
    """Ссылка «spotify:track:…» по названию — без Spotify API и Premium."""
    site = f"open.spotify.com/{kind}" if kind else "open.spotify.com"
    for hit in search(f"site:{site} {query}", limit=8):
        m = _SPOTIFY_RE.search(hit.url)
        if m and (kind is None or m.group(1) == kind):
            return f"spotify:{m.group(1)}:{m.group(2)}"
    return None


# ── фильмы ────────────────────────────────────────────────────────────────────

PROVIDERS = {
    # имя → (домен для site:, шаблон страницы-фильма в URL, страница поиска)
    "vk": ("vkvideo.ru", r"vkvideo\.ru/video-?\d+_\d+|vk\.com/video-?\d+_\d+",
           "https://vkvideo.ru/?q={q}"),
    "kinopoisk": ("hd.kinopoisk.ru", r"(?:hd\.)?kinopoisk\.ru/(?:film|series)/\d+",
                  "https://hd.kinopoisk.ru/search?text={q}"),
    "ivi": ("ivi.ru", r"ivi\.ru/watch/[\w-]+", "https://www.ivi.ru/search/?q={q}"),
    "okko": ("okko.tv", r"okko\.tv/(?:movie|serial)/[\w-]+", "https://okko.tv/search/{q}"),
    "youtube": ("youtube.com", r"youtube\.com/watch\?v=[\w-]{11}",
                "https://www.youtube.com/results?search_query={q}"),
}


def film_page(title: str, provider: str) -> str | None:
    """Прямая страница фильма у провайдера (там запускается плеер)."""
    domain, pattern, _ = PROVIDERS[provider]
    rx = re.compile(pattern)
    for hit in search(f"site:{domain} {title}", limit=8):
        m = rx.search(hit.url)
        if m:
            url = hit.url if hit.url.startswith("http") else "https://" + m.group(0)
            return url
    return None


def search_page(title: str, provider: str) -> str:
    return PROVIDERS[provider][2].format(q=urllib.parse.quote(title))
