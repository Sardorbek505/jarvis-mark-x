"""
Действие: YouTube — включить, узнать, о чём ролик, пересказать его.

ЗАЧЕМ ПЕРЕСКАЗ
    Двадцатиминутный ролик ради одной мысли — обычное дело. Субтитры у
    большинства роликов есть (свои или автоматические), и пересказ по ним
    занимает секунды и не требует открывать видео вовсе. Это единственное
    здесь, чего нельзя сделать руками за то же время.

ЧТО БЕЗ КЛЮЧЕЙ
    Название и автор берутся через oEmbed — открытая точка YouTube, ключ ей не
    нужен. Данных о просмотрах и длительности она не отдаёт, и мы их не
    выдумываем: сказать «не знаю» дешевле, чем ошибиться в числе.

ЧЕГО ЗДЕСЬ НЕТ
    «Тренды» не реализованы сознательно. Без ключа YouTube Data API их можно
    только выскрести из разметки главной страницы, а она меняется без
    предупреждения — инструмент ломался бы молча и в самый неподходящий момент.
    Такой запрос уходит в web_search, где есть чем ответить.
"""

import json
import logging
import re
import urllib.parse
import urllib.request

_logger = logging.getLogger(__name__)

_TIMEOUT = 12

# Столько символов расшифровки отдаём модели. Часовой ролик — это тысяч
# сорок знаков; всё сразу и незачем, и дорого.
_MAX_TRANSCRIPT = 14000

# Языки субтитров по порядку предпочтения: сначала те, на которых говорит
# пользователь, потом английский как самый частый.
_LANGS = ("ru", "uz", "kk", "en")

_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/|live/))([A-Za-z0-9_-]{11})"
)


def _video_id(текст: str) -> str:
    """Идентификатор ролика из ссылки — или пустая строка."""
    текст = (текст or "").strip()
    совпадение = _ID_RE.search(текст)
    if совпадение:
        return совпадение.group(1)
    # Голый идентификатор тоже принимаем: модель нередко передаёт именно его.
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", текст):
        return текст
    return ""


def _search_url(запрос: str) -> str:
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote(запрос)


def _watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _open(url: str) -> bool:
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            import os
            os.startfile(url)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
        return True
    except Exception as exc:
        _logger.warning("Браузер не открылся: %s", exc)
        return False


def _oembed(vid: str) -> dict:
    """Название и автор ролика. Ключ не нужен, но и данных немного."""
    url = ("https://www.youtube.com/oembed?format=json&url="
           + urllib.parse.quote(_watch_url(vid), safe=""))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        _logger.warning("oEmbed не ответил: %s", exc)
        return {}


def _transcript(vid: str) -> tuple[str, str]:
    """Расшифровка ролика и язык. Пустая строка — субтитров нет.

    Второй элемент при неудаче содержит причину человеческими словами: «нет
    субтитров» и «видео недоступно» — разные новости для того, кто спрашивал."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return "", "модуль расшифровки не установлен (pip install youtube-transcript-api)"

    try:
        api = YouTubeTranscriptApi()
        расшифровка = api.fetch(vid, languages=_LANGS)
    except Exception as exc:
        имя = type(exc).__name__
        if "TranscriptsDisabled" in имя or "NoTranscriptFound" in имя:
            return "", "у этого ролика нет субтитров"
        if "VideoUnavailable" in имя or "InvalidVideoId" in имя:
            return "", "ролик недоступен"
        if "IpBlocked" in имя or "RequestBlocked" in имя:
            return "", "YouTube не отдаёт субтитры с этого адреса"
        _logger.warning("Расшифровка не получена (%s): %s", имя, exc)
        return "", f"субтитры не достались: {str(exc)[:80]}"

    куски = []
    for отрывок in расшифровка:
        текст = getattr(отрывок, "text", None)
        if текст is None and isinstance(отрывок, dict):
            текст = отрывок.get("text", "")
        if текст:
            куски.append(str(текст))

    язык = getattr(расшифровка, "language_code", "") or ""
    return " ".join(куски)[:_MAX_TRANSCRIPT], язык


def _summarise(текст: str, название: str) -> str:
    """Пересказ расшифровки. Пустая строка — модель недоступна."""
    try:
        from core.onboarding import ensure_gemini_key
        ключ = ensure_gemini_key(interactive=False)
    except Exception as exc:
        _logger.debug("Ключ недоступен: %s", exc)
        return ""
    if not ключ:
        return ""

    try:
        from google import genai
        from google.genai import types

        клиент = genai.Client(api_key=ключ)
        ответ = клиент.models.generate_content(
            model="gemini-2.5-flash",
            contents=(
                "Ниже расшифровка видео с YouTube. Перескажи её по-русски: о чём "
                "ролик и какие главные мысли в нём звучат. Четыре-шесть "
                "предложений, без списков и markdown — это читают вслух. "
                "Обращайся «сэр». Не добавляй ничего, чего в расшифровке нет.\n\n"
                f"Название: {название}\n\nРасшифровка:\n{текст}"
            ),
            config=types.GenerateContentConfig(temperature=0.3, max_output_tokens=600),
        )
        return (ответ.text or "").strip()
    except Exception as exc:
        _logger.warning("Пересказ не получился: %s", exc)
        return ""


# ─── Действия ─────────────────────────────────────────────────────────────────

def _play(запрос: str, player=None) -> str:
    vid = _video_id(запрос)
    if vid:
        url, что = _watch_url(vid), "ролик"
    elif запрос:
        url, что = _search_url(запрос), f"поиск «{запрос}»"
    else:
        return "Что включить на YouTube, сэр?"

    if not _open(url):
        return "Не смог открыть браузер, сэр."
    if player:
        player.write_log(f"SYS: YouTube — {что}")
    return ("Включил ролик." if vid
            else f"Открыл поиск «{запрос}» на YouTube — выберите нужное.")


def _info(ссылка: str, player=None) -> str:
    vid = _video_id(ссылка)
    if not vid:
        return "Дайте ссылку на ролик, сэр."

    данные = _oembed(vid)
    if not данные:
        return "Не смог узнать, что это за ролик — YouTube не ответил."

    название = данные.get("title", "без названия")
    автор = данные.get("author_name", "")
    if player:
        player.write_log(f"SYS: YouTube — {название[:40]}")
    return (f"«{название}»" + (f", канал {автор}." if автор else ".")
            + " Про длительность и просмотры сказать не могу — их эта справка не отдаёт.")


def _summary(ссылка: str, player=None) -> str:
    vid = _video_id(ссылка)
    if not vid:
        return "Дайте ссылку на ролик, сэр — по названию пересказать не смогу."

    данные = _oembed(vid)
    название = данные.get("title", "")

    текст, беда = _transcript(vid)
    if not текст:
        # Прямая причина вместо «не получилось»: «нет субтитров» и «ролик
        # недоступен» требуют от человека разных действий.
        return f"Пересказать не выйдет, сэр: {беда}."

    if player:
        player.write_log(f"SYS: пересказываю «{название[:40]}»")

    пересказ = _summarise(текст, название)
    if пересказ:
        return (f"«{название}»: {пересказ}" if название else пересказ)

    # Модели нет — отдаём начало расшифровки. Это хуже пересказа, но честнее
    # молчания: человек хотя бы узнает, о чём там речь.
    # Обрезать по пробелу можно, только если текст длиннее предела: иначе у
    # короткой расшифровки отрезается последнее слово.
    сжатый = " ".join(текст.split())
    начало = (сжатый if len(сжатый) <= 400
              else сжатый[:400].rsplit(" ", 1)[0] + "…")
    return f"Пересказать не смог — модель недоступна. Вот начало расшифровки, сэр: {начало}"


def youtube_video(parameters: dict, player=None) -> str:
    параметры = parameters or {}
    действие = str(параметры.get("action", "play")).strip().lower()
    запрос = str(параметры.get("query", "")).strip()
    ссылка = str(параметры.get("url", "")).strip() or запрос

    if действие in ("summarize", "summary", "пересказ", "перескажи"):
        return _summary(ссылка, player)
    if действие in ("get_info", "info", "информация"):
        return _info(ссылка, player)
    if действие in ("trending", "тренды"):
        # Честнее отправить к поиску, чем выскребать главную страницу, которая
        # меняется без предупреждения.
        return ("Тренды YouTube я не умею, сэр — спросите про них веб-поиском, "
                "он ответит свежими данными.")
    return _play(запрос or ссылка, player)


# ─── Объявление для реестра действий ──────────────────────────────────────────
TOOL = {
    "name": "youtube_video",
    "description": (
        "Работа с YouTube: включить ролик или поиск по названию (action=play), "
        "узнать название и канал по ссылке (action=get_info) и — главное — "
        "ПЕРЕСКАЗАТЬ ролик по субтитрам, не открывая его (action=summarize). "
        "Пересказ вызывай, когда пользователь спрашивает «о чём это видео», "
        "«перескажи ролик», «что там говорят» и даёт ссылку. "
        "Для фильмов и сериалов используй movie_player, для музыки — music_player."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play (по умолчанию) | summarize | get_info",
            },
            "query": {
                "type": "STRING",
                "description": "Что искать на YouTube (для action=play)",
            },
            "url": {
                "type": "STRING",
                "description": "Ссылка на ролик — нужна для summarize и get_info",
            },
        },
        "required": [],
    },
    "handler": youtube_video,
}
