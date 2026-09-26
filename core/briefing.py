"""Утренний брифинг — коротко и лично.

Раньше брифинг зачитывался при КАЖДОМ запуске Джарвиса с 6 до 11 (и после
перезапуска — снова), длинным монологом «произнеси дословно», а новости не
приходили никогда: звался несуществующий NewsManager.fetch_all_news.

Теперь:
- один раз в день, в первый разговор утром — после того как Джарвис ответил
  на вашу первую просьбу (не перебивая её), и только после вашего подъёма
  из «Обо мне» (по умолчанию 6:00), до полудня;
- «Доброе утро», «что сегодня» — в любой момент (инструмент morning_briefing);
- модель получает ФАКТЫ и говорит своими словами, в вашем стиле, до ~40 с:
  погода в вашем городе, будильники и таймеры, дела из календаря, новые
  сообщения от близких, день рождения, новости по вашим темам.
Каждый источник — отдельно и с запасом по времени: упал один — остальное есть.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

NOON = 12


def _about() -> dict:
    try:
        from core import about_me
        return about_me.answers()
    except Exception:
        return {}


def wake_hour(about: dict | None = None) -> float:
    """Подъём из «Обо мне» («7:30», «в 7», «полвосьмого» не поймём → 6)."""
    raw = (about if about is not None else _about()).get("wake_time", "")
    m = re.search(r"(\d{1,2})(?:[:.](\d{2}))?", raw or "")
    if not m:
        return 6.0
    h = int(m.group(1)) + int(m.group(2) or 0) / 60
    return h if 3 <= h <= 13 else 6.0


# ── источники ────────────────────────────────────────────────────────────────

def _weather(about: dict) -> str:
    from actions.weather import weather_action
    city = about.get("city") or ""
    return weather_action({"city": city} if city else {}) or ""


def _clock(now: datetime) -> str:
    from core.clock import clock
    c = clock()
    parts = []
    today = [a for a in c.alarms if (c.next_ring(a) or datetime.max).date() == now.date()]
    if today:
        parts.append("будильники сегодня: " + ", ".join(sorted(a.time for a in today)))
    timers = c.timer_list()
    if timers and not timers.startswith("Таймеров нет"):
        parts.append(timers)
    return "; ".join(parts)


def _calendar() -> str:
    from core.calendar_manager import get_events
    text = (get_events("today") or "").strip()
    return "" if not text or "нет" in text.lower()[:40] else text


def _messages() -> str:
    from core.contacts import contacts
    c = contacts()
    if not c.me.linked() or not c.book.contacts:
        return ""
    text = c.unread()
    return "" if text.startswith(("Новых сообщений", "Не могу")) else text


def _birthday(about: dict, today: date) -> str:
    raw = (about.get("birthday") or "").lower()
    months = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]
    m = re.search(r"(\d{1,2})\s*([а-я]+)", raw)
    if m:
        day, word = int(m.group(1)), m.group(2)
        month = next((i + 1 for i, s in enumerate(months) if word.startswith(s)), 0)
        if (day, month) == (today.day, today.month):
            return "СЕГОДНЯ ДЕНЬ РОЖДЕНИЯ ПОЛЬЗОВАТЕЛЯ — поздравь первым делом, тепло."
    m = re.search(r"(\d{1,2})[./](\d{1,2})", raw)
    if m and (int(m.group(1)), int(m.group(2))) == (today.day, today.month):
        return "СЕГОДНЯ ДЕНЬ РОЖДЕНИЯ ПОЛЬЗОВАТЕЛЯ — поздравь первым делом, тепло."
    return ""


def _news(about: dict) -> str:
    from core.news_manager import NewsManager
    items = NewsManager().get_personalized_news(limit=12) or []
    topics = [t.strip().lower() for t in re.split(r"[,;]", about.get("news", "")) if t.strip()]
    if topics:
        liked = [a for a in items if any(t[:5] in (a.get("title", "") + " " + a.get("category", "")).lower()
                                         for t in topics)]
        items = liked or items
    titles = [a.get("title", "").strip() for a in items if a.get("title")][:3]
    return " | ".join(titles)


def gather(now: datetime | None = None, sources: dict[str, Callable] | None = None,
           timeout: float = 12.0) -> dict[str, str]:
    """Собрать факты параллельно; что не успело — пропускаем."""
    now = now or datetime.now()
    about = _about()
    jobs = sources or {
        "погода": lambda: _weather(about),
        "часы": lambda: _clock(now),
        "дела": _calendar,
        "сообщения": _messages,
        "новости": lambda: _news(about),
    }
    out: dict[str, str] = {}
    threads = []
    for name, fn in jobs.items():
        def run(name=name, fn=fn):
            try:
                v = (fn() or "").strip()
                if v:
                    out[name] = v[:600]
            except Exception as exc:
                logger.debug("Брифинг, %s: %s", name, exc)
        t = threading.Thread(target=run, daemon=True, name=f"brief-{name}")
        t.start()
        threads.append(t)
    end = datetime.now().timestamp() + timeout
    for t in threads:
        t.join(max(0.0, end - datetime.now().timestamp()))
    bd = _birthday(about, now.date())
    if bd:
        out["праздник"] = bd
    return {k: out[k] for k in ("праздник", "погода", "часы", "дела", "сообщения", "новости") if k in out}


def instruction(facts: dict[str, str], now: datetime | None = None, asked: bool = False) -> str:
    now = now or datetime.now()
    about = _about()
    addr = about.get("address_as") or "сэр"
    style = about.get("talk_style") or "коротко, с лёгкой иронией"
    part = "доброе утро" if now.hour < 12 else ("добрый день" if now.hour < 18 else "добрый вечер")
    body = "\n".join(f"- {k}: {v}" for k, v in facts.items()) or "- новостей и дел нет — спокойный день"
    return ("[СИСТЕМА: " + ("пользователь попросил брифинг. " if asked else
                            "первый разговор сегодня утром — после ответа на его просьбу добавь брифинг. ")
            + f"Скажи «{part}», обращайся «{addr}», стиль — {style}. Своими словами, живо, до 40 секунд: "
            "самое важное первым, цифры — округляй, списки не зачитывай. Ничего не выдумывай сверх фактов. "
            "В конце одной фразой предложи что-то полезное (включить музыку, поставить таймер).\n"
            f"Факты:\n{body}]")


# ── раз в день ───────────────────────────────────────────────────────────────

def _state_path() -> Path:
    env = os.getenv("JARVIS_BRIEFING_STATE", "").strip()
    if env:
        return Path(env)
    from core.paths import get_data_root
    return Path(get_data_root()) / "briefing_state.json"


def due(now: datetime | None = None) -> bool:
    """Пора ли утренний брифинг: после подъёма, до полудня, сегодня ещё не было."""
    if os.getenv("JARVIS_BRIEFING", "1") == "0":
        return False
    now = now or datetime.now()
    hour = now.hour + now.minute / 60
    if not (wake_hour() <= hour < NOON):
        return False
    try:
        last = json.loads(_state_path().read_text(encoding="utf-8")).get("last", "")
    except Exception:
        last = ""
    return last != now.date().isoformat()


def mark_done(now: datetime | None = None) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"last": (now or datetime.now()).date().isoformat()}), encoding="utf-8")
    except OSError as exc:
        logger.debug("Брифинг, состояние: %s", exc)


def briefing_tool(_p: dict | None = None) -> str:
    """Инструмент morning_briefing: факты для ответа своими словами."""
    mark_done()
    return instruction(gather(), asked=True).replace("[СИСТЕМА: ", "", 1).rstrip("]")
