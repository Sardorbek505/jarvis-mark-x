"""«Запомни это»: снимок окна → главное из него → заметка в Obsidian.

«Джарвис, запомни это» над статьёй, заказом, ошибкой, расписанием — и через
неделю «что я запоминал про заказ?» найдёт (obsidian search). Заметка:
заголовок, 2–3 предложения сути, факты (номера, даты, суммы, имена), ссылки,
метки и сам снимок рядом (картинкой в папке attachments).

Что именно важно, решает Gemini по снимку; focus — если человек уточнил
(«запомни номер заказа», «запомни эту статью»). Снимок — активного окна
(обычно этого и просят), source=screen — весь экран.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

FOLDER = "Запомнил"
MODEL = "gemini-2.5-flash"
PROMPT = """Пользователь сказал «запомни это» — на снимке то, что он хочет сохранить.
Выпиши главное, чтобы через месяц он вспомнил и нашёл. Верни ТОЛЬКО JSON:
{"title": "короткий понятный заголовок (до 60 знаков)",
 "summary": "2–3 предложения: что это и зачем может понадобиться",
 "facts": ["точные детали: номера, даты, суммы, адреса, имена, коды, версии — как на экране"],
 "links": ["адреса сайтов, если видны в адресной строке или тексте"],
 "tags": ["2–4 метки одним словом, по-русски"]}
Только то, что реально видно; ничего не выдумывай. Пиши по-русски (цитаты и коды — как есть)."""


def parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    d = json.loads(m.group(0) if m else text)

    def strs(v) -> list[str]:
        return [" ".join(str(x).split()) for x in (v or []) if str(x).strip()][:12]
    title = " ".join(str(d.get("title") or "").split())[:80]
    summary = " ".join(str(d.get("summary") or "").split())
    if not title and not summary:
        raise ValueError("на снимке нечего запомнить")
    return {"title": title or summary[:60], "summary": summary, "facts": strs(d.get("facts")),
            "links": strs(d.get("links")), "tags": [re.sub(r"\W+", "_", t.lower()).strip("_")
                                                      for t in strs(d.get("tags"))][:4]}


def to_markdown(d: dict, when: datetime, window: str = "", image: str = "", focus: str = "") -> str:
    tags = ["запомнил"] + [t for t in d["tags"] if t and t != "запомнил"]
    lines = ["---", f"created: {when:%Y-%m-%d %H:%M}", "source: jarvis-remember",
             "tags: [" + ", ".join(tags) + "]", "---", "", f"# {d['title']}", ""]
    if d["summary"]:
        lines += [d["summary"], ""]
    if focus:
        lines += [f"> Просили запомнить: {focus}", ""]
    if d["facts"]:
        lines += ["## Детали", *[f"- {f}" for f in d["facts"]], ""]
    if d["links"]:
        lines += ["## Ссылки", *[f"- {u}" for u in d["links"]], ""]
    if window:
        lines += [f"Окно: {window}", ""]
    if image:
        lines += [f"![[{image}]]", ""]
    return "\n".join(lines)


def _capture(source: str) -> bytes | None:
    from actions import vision
    if source == "screen":
        return vision.capture_screen_jpeg(max_size=1800, quality=85)
    return vision.capture_active_window_jpeg() or vision.capture_screen_jpeg(max_size=1800, quality=85)


def _ask(jpeg: bytes, focus: str) -> str:
    from google.genai import types

    from actions import vision
    key = vision._get_api_key()
    if not key:
        raise RuntimeError("нет ключа Gemini")
    prompt = PROMPT + (f"\nОсобенно важно: {focus}." if focus else "")
    resp = vision._client(key).models.generate_content(
        model=MODEL, contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0.2, max_output_tokens=1200,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH))
    return resp.text or ""


def _window_title() -> str:
    try:
        from core import win_apps
        w = win_apps.foreground()
        return (w.title if w else "")[:120]
    except Exception:
        return ""


def _vault() -> Path:
    from actions import obsidian
    vault, _cfg = obsidian._ensure_vault()
    return vault


def remember_screen(p: dict | None = None, *, capture: Callable[[str], bytes | None] = _capture,
                    ask: Callable[[bytes, str], str] = _ask, vault: Callable[[], Path] = _vault,
                    window: Callable[[], str] = _window_title, now: Callable[[], datetime] = datetime.now) -> str:
    from actions.obsidian import _slugify, _unique_path, _write_md
    p = p or {}
    source = "screen" if str(p.get("source") or "").lower() == "screen" else "window"
    focus = " ".join(str(p.get("focus") or "").split())[:200]
    title_now = window()
    jpeg = capture(source)
    if not jpeg:
        return "Не получилось сделать снимок экрана."
    try:
        d = parse(ask(jpeg, focus))
    except Exception as exc:
        logger.warning("Запомни это: %s", exc)
        return f"Снимок сделал, но разобрать не вышло: {exc}."
    when = now()
    folder = vault() / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique_path(folder, _slugify(d["title"]))
    image = ""
    if p.get("keep_image", True) is not False:
        att = folder / "attachments"
        att.mkdir(exist_ok=True)
        img = att / f"{path.stem} {when:%Y-%m-%d %H-%M-%S}.jpg"
        img.write_bytes(jpeg)
        image = f"attachments/{img.name}"
    _write_md(path, to_markdown(d, when, title_now, image, focus))
    logger.info("Запомни это: %s", path.name)
    facts = "; ".join(d["facts"][:3])
    return (f"Запомнил: «{d['title']}». {d['summary']}" + (f" Детали: {facts}." if facts else "")
            + f" Заметка в Obsidian, папка «{FOLDER}». Скажи пользователю одной-двумя фразами, что запомнил.")
