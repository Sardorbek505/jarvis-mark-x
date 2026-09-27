"""Любимый клуб: ближайшие матчи, счёт вживую, итоги, важные новости.

Клуб — из «Обо мне» («За какой клуб болеете?») или голосом («я болею за
Реал»). Дальше Джарвис сам:
  • утром в брифинге — «сегодня играет Реал в 22:00»;
  • за REMIND_MIN минут до матча — напоминание;
  • во время матча — «Гол! Реал 1:0» (счёт и в капсуле), после — итог;
  • важные новости клуба (трансферы, травмы, тренер, контракты) — в капсулу.
Голосом — инструмент football: когда играет, какой счёт, как сыграли, новости.

Данные — открытые, без ключей: расписание и счёт — ESPN (site.api.espn.com,
по всем турнирам клуба), новости — Google Новости на русском. Нет сети —
просто молчит. Смотреть матчи Джарвис не включает — только помогает не
пропустить.

Состояние (клуб, что уже сказано) — football.json в папке данных.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
LEAGUES = ["esp.1", "eng.1", "ger.1", "ita.1", "fra.1", "por.1", "ned.1", "tur.1", "rus.1", "uefa.champions",
           "uefa.europa"]
CUPS = ["uefa.champions", "uefa.europa", "uefa.europa.conf"]
REMIND_MIN = 30
LIVE_BEFORE = timedelta(minutes=10)
LIVE_AFTER = timedelta(hours=2, minutes=40)
FIXTURES_TTL = 6 * 3600
NEWS_EVERY = 3 * 3600

# Как клуб называют по-русски → как он записан у ESPN, и как искать новости.
CLUBS = {
    "реал": ("Real Madrid", "Реал Мадрид"), "реал мадрид": ("Real Madrid", "Реал Мадрид"),
    "барса": ("Barcelona", "Барселона"), "барселона": ("Barcelona", "Барселона"),
    "атлетико": ("Atletico Madrid", "Атлетико"), "севилья": ("Sevilla", "Севилья"),
    "манчестер сити": ("Manchester City", "Манчестер Сити"), "сити": ("Manchester City", "Манчестер Сити"),
    "манчестер юнайтед": ("Manchester United", "Манчестер Юнайтед"), "мю": ("Manchester United", "Манчестер Юнайтед"),
    "ливерпуль": ("Liverpool", "Ливерпуль"), "арсенал": ("Arsenal", "Арсенал"), "челси": ("Chelsea", "Челси"),
    "тоттенхэм": ("Tottenham", "Тоттенхэм"), "бавария": ("Bayern Munich", "Бавария"),
    "боруссия": ("Borussia Dortmund", "Боруссия Дортмунд"), "пари сен-жермен": ("Paris Saint-Germain", "ПСЖ"),
    "псж": ("Paris Saint-Germain", "ПСЖ"), "ювентус": ("Juventus", "Ювентус"), "интер": ("Internazionale", "Интер"),
    "милан": ("AC Milan", "Милан"), "наполи": ("Napoli", "Наполи"), "рома": ("AS Roma", "Рома"),
    "бенфика": ("Benfica", "Бенфика"), "порту": ("FC Porto", "Порту"), "аякс": ("Ajax", "Аякс"),
    "галатасарай": ("Galatasaray", "Галатасарай"), "фенербахче": ("Fenerbahce", "Фенербахче"),
    "зенит": ("Zenit St Petersburg", "Зенит"), "спартак": ("Spartak Moscow", "Спартак"),
    "цска": ("CSKA Moscow", "ЦСКА"), "локомотив": ("Lokomotiv Moscow", "Локомотив"),
    "краснодар": ("FK Krasnodar", "Краснодар"), "динамо": ("Dynamo Moscow", "Динамо Москва"),
}
IMPORTANT = re.compile(r"трансфер|перешел|перешёл|подписал|контракт|травм|повреждени|тренер|отставк|уволен|"
                       r"назначен|капитан|дисквалиф|продлил|аренд|покинет|уход", re.I)


@dataclass
class Match:
    id: str
    when: datetime                       # местное время начала
    home: str
    away: str
    home_score: str = ""
    away_score: str = ""
    state: str = "pre"                   # pre | in | post
    detail: str = ""                     # «45'», «FT», «Перерыв»
    league: str = ""
    home_abbr: str = ""                  # «RMA» — для капсулы и карточки
    away_abbr: str = ""
    home_color: str = ""                 # цвет формы с ESPN, «00529f»
    away_color: str = ""

    def score(self) -> str:
        return f"{self.home} {self.home_score}:{self.away_score} {self.away}"

    def title(self) -> str:
        return f"{self.home} — {self.away}"


def _get(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Jarvis/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str) -> dict:
    return json.loads(_get(url).decode("utf-8"))


def _norm(s: str) -> str:
    return re.sub(r"[^a-zа-яё0-9 ]", "", (s or "").lower().replace("ё", "е")).strip()


def club_names(query: str) -> tuple[str, str]:
    """«Реал» → («Real Madrid», «Реал Мадрид»); неизвестный — как сказали."""
    q = _norm(query)
    for k, v in CLUBS.items():
        kk = _norm(k)
        # «барсу», «реалу», «ливерпулю» — падежи: основа та же, окончание другое
        if q == kk or (len(kk) >= 4 and " " not in kk and q[:len(kk) - 1] == kk[:-1] and len(q) - len(kk) <= 2):
            return v
    for k, v in CLUBS.items():
        if _norm(k) in q or q in _norm(v[1]) or q == _norm(v[0]):    # и «Real Madrid» с ESPN
            return v
    return query.strip(), query.strip()


def _score(c: dict) -> str:
    s = c.get("score", "")
    if isinstance(s, dict):
        s = s.get("displayValue") or s.get("value", "")
        if isinstance(s, float) and s.is_integer():
            s = int(s)
    return str(s) if s not in (None, "") else ""


def parse_event(e: dict) -> Match | None:
    """Событие ESPN (расписание или табло) → Match."""
    try:
        comp = (e.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        home, away = teams.get("home", {}), teams.get("away", {})
        when = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00")).astimezone()
        st = (comp.get("status") or e.get("status") or {}).get("type", {})
        league = ((e.get("league") or {}).get("name") or (e.get("season") or {}).get("name")
                  or (comp.get("league") or {}).get("name") or "")
        ht, at = home.get("team") or {}, away.get("team") or {}
        return Match(id=str(e.get("id")), when=when.replace(tzinfo=None),
                     home=ht.get("displayName", ""), away=at.get("displayName", ""),
                     home_score=_score(home), away_score=_score(away),
                     state=st.get("state", "pre"), detail=st.get("shortDetail") or st.get("detail") or "",
                     league=league, home_abbr=ht.get("abbreviation", ""), away_abbr=at.get("abbreviation", ""),
                     home_color=str(ht.get("color") or ""), away_color=str(at.get("color") or ""))
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        logger.debug("Футбол, событие: %s", exc)
        return None


@dataclass
class News:
    title: str
    link: str
    source: str
    when: datetime | None


def parse_rss(xml: bytes) -> list[News]:
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            when = None
        if title:
            out.append(News(title, (it.findtext("link") or "").strip(), src, when))
    return out


class Football:
    def __init__(self, path: Path, now: Callable[[], datetime] = datetime.now, get_json=_json, get_raw=_get):
        self.path = Path(path)
        self.now = now
        self.get_json, self.get_raw = get_json, get_raw
        self.say: Callable[[str], None] = lambda text: None
        self.notify: Callable[[str, str], None] = lambda title, text: None
        self.state: dict = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._fixtures: list[Match] = []
        self._fixtures_at = 0.0
        self._mtime = (0, 0)
        self.current: Match | None = None           # идущий матч — капсула и экран «Футбол»
        self.load()

    # ── файл ──
    def _stat(self) -> tuple:
        try:
            st = self.path.stat()
            return st.st_mtime_ns, st.st_size
        except OSError:
            return (0, 0)

    def refresh(self):
        """Клуб поменяли в другом процессе (pc_server — анкета с телефона)."""
        if self._stat() != self._mtime:
            club = self.state.get("club")
            self.load()
            if self.state.get("club") != club:
                self._fixtures, self._fixtures_at = [], 0.0

    def load(self):
        self._mtime = self._stat()
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.state = {}
        self.state.setdefault("club", "")
        self.state.setdefault("team", {})           # {"id", "name", "league"}
        self.state.setdefault("goals", True)
        self.state.setdefault("news", True)
        self.state.setdefault("remind_min", REMIND_MIN)
        self.state.setdefault("sent", {})           # что уже сказано: ключ → когда
        self.state.setdefault("scores", {})         # последний известный счёт матча
        self.state.setdefault("seen_news", [])
        self.state.setdefault("news_at", 0.0)

    def save(self):
        with self._lock:
            cut = (self.now() - timedelta(days=7)).isoformat()
            self.state["sent"] = {k: v for k, v in self.state["sent"].items() if v >= cut}
            self.state["scores"] = dict(list(self.state["scores"].items())[-20:])
            self.state["seen_news"] = self.state["seen_news"][-200:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
            self._mtime = self._stat()

    # ── клуб ──
    def set_club(self, query: str) -> str:
        query = " ".join(str(query or "").split())
        with self._lock:
            self.state["club"], self.state["team"] = query, {}
            self._fixtures, self._fixtures_at = [], 0.0
            self.save()
        if not query:
            return "Любимый клуб убрал — про матчи напоминать не буду."
        team = self.team()
        name = club_names(query)[1]
        if not team:
            return f"Запомнил: {name}. Расписание пока не нашёл — проверю, когда будет сеть."
        return f"Запомнил: болеете за «{name}». Буду напоминать о матчах и важных новостях."

    def team(self) -> dict:
        """Клуб у ESPN: ищем по лигам один раз и запоминаем."""
        if self.state["team"].get("id") or not self.state["club"]:
            return self.state["team"]
        eng, _ru = club_names(self.state["club"])
        want = _norm(eng)
        for league in LEAGUES:
            try:
                data = self.get_json(f"{ESPN}/{league}/teams")
            except Exception as exc:
                logger.debug("Футбол, лига %s: %s", league, exc)
                continue
            for lg in (data.get("sports") or [{}])[0].get("leagues", []):
                for t in lg.get("teams", []):
                    t = t.get("team", t)
                    names = [_norm(t.get(k, "")) for k in ("displayName", "shortDisplayName", "name", "location")]
                    if want in names or any(want and n and (want in n or n in want) and len(n) > 3 for n in names):
                        team = {"id": str(t.get("id")), "name": t.get("displayName", eng), "league": league}
                        with self._lock:
                            self.state["team"] = team
                            self.save()
                        return team
        return {}

    # ── матчи ──
    def fixtures(self, force: bool = False) -> list[Match]:
        """Все матчи клуба (прошедшие и будущие) по его лиге и еврокубкам."""
        team = self.team()
        if not team:
            return []
        if not force and self._fixtures and time.monotonic() - self._fixtures_at < FIXTURES_TTL:
            return self._fixtures
        seen: dict[str, Match] = {}
        for league in dict.fromkeys([team["league"], *CUPS]):
            for fixture in ("?fixture=true", ""):
                try:
                    data = self.get_json(f"{ESPN}/{league}/teams/{team['id']}/schedule{fixture}")
                except Exception as exc:
                    logger.debug("Футбол, расписание %s: %s", league, exc)
                    continue
                for e in data.get("events", []):
                    m = parse_event(e)
                    if m and m.home and m.away:
                        seen[m.id] = m
        if seen:
            self._fixtures = sorted(seen.values(), key=lambda m: m.when)
            self._fixtures_at = time.monotonic()
        return self._fixtures

    def next_match(self) -> Match | None:
        now = self.now()
        return next((m for m in self.fixtures() if m.state != "post" and m.when + LIVE_AFTER > now), None)

    def last_match(self) -> Match | None:
        done = [m for m in self.fixtures() if m.state == "post" or m.when + LIVE_AFTER < self.now()]
        return done[-1] if done else None

    def live(self, m: Match) -> Match:
        """Свежий счёт матча — с табло лиги / турнира."""
        team = self.state["team"]
        for league in dict.fromkeys([team.get("league", ""), *CUPS]):
            if not league:
                continue
            try:
                data = self.get_json(f"{ESPN}/{league}/scoreboard")
            except Exception as exc:
                logger.debug("Футбол, табло %s: %s", league, exc)
                continue
            for e in data.get("events", []):
                if str(e.get("id")) == m.id:
                    return parse_event(e) or m
        return m

    # ── новости ──
    def news(self, important_only: bool = False, limit: int = 5) -> list[News]:
        if not self.state["club"]:
            return []
        _eng, ru = club_names(self.state["club"])
        q = urllib.parse.quote(f'"{ru}" футбол')
        try:
            items = parse_rss(self.get_raw(f"https://news.google.com/rss/search?q={q}&hl=ru&gl=RU&ceid=RU:ru"))
        except Exception as exc:
            logger.debug("Футбол, новости: %s", exc)
            return []
        day_ago = self.now() - timedelta(days=1)
        items = [n for n in items if not n.when or n.when > day_ago]
        if important_only:
            items = [n for n in items if IMPORTANT.search(n.title)]
        return items[:limit]

    def overview(self, news: int = 6) -> dict:
        """Всё для экрана «Футбол» одним вызовом (сеть — зовите не из потока окна)."""
        if not self.state["club"]:
            return {"club": ""}
        ms, now = self.fixtures(), self.now()
        nxt = self.next_match()
        if nxt and nxt.when - LIVE_BEFORE <= now:
            nxt = self.live(nxt)
            self.current = nxt if nxt.state == "in" else self.current
        done = [m for m in ms if m.state == "post" or m.when + LIVE_AFTER < now]
        ahead = [m for m in ms if m not in done and m is not nxt and (not nxt or m.id != nxt.id)]
        return {"club": self.state["club"], "name": club_names(self.state["club"])[1],
                "team": self.state["team"].get("name", ""), "next": nxt, "results": done[-5:][::-1],
                "upcoming": ahead[:5], "news": self.news(limit=news), "goals": self.state["goals"],
                "news_on": self.state["news"], "now": now}

    # ── словами ──
    @staticmethod
    def when_text(m: Match, now: datetime) -> str:
        d = (m.when.date() - now.date()).days
        day = {0: "сегодня", 1: "завтра", -1: "вчера"}.get(d, f"{m.when:%d.%m}")
        return f"{day} в {m.when:%H:%M}"

    def next_text(self) -> str:
        if not self.state["club"]:
            return "Любимый клуб не указан — скажите «я болею за …» или впишите в «Обо мне»."
        m = self.next_match()
        if not m:
            return "Ближайших матчей не нашёл (или нет связи с расписанием)."
        if m.state == "in":
            m = self.live(m)
            return f"Идёт матч: {m.score()} ({m.detail})."
        return f"Ближайший матч: {m.title()} — {self.when_text(m, self.now())}" + (f", {m.league}" if m.league else "") + "."

    def last_text(self) -> str:
        m = self.last_match()
        if not m:
            return "Прошедших матчей не нашёл."
        return f"Последний матч {self.when_text(m, self.now())}: {m.score()}" + (f", {m.league}" if m.league else "") + "."

    def news_text(self) -> str:
        items = self.news(limit=5)
        if not items:
            return "Свежих новостей клуба не нашёл."
        return "Новости: " + " | ".join(f"{n.title} ({n.source})" if n.source else n.title for n in items)

    def briefing(self) -> str:
        """Для утреннего брифинга: матч сегодня/завтра и вчерашний итог."""
        if not self.state["club"]:
            return ""
        now, parts = self.now(), []
        last = self.last_match()
        if last and (now - last.when) < timedelta(hours=30):
            parts.append(f"вчера/сегодня ночью: {last.score()}")
        m = self.next_match()
        if m and (m.when.date() - now.date()).days <= 1:
            parts.append(f"{m.title()} {self.when_text(m, now)}")
        return "; ".join(parts)

    # ── сам следит ──
    def _once(self, key: str) -> bool:
        if key in self.state["sent"]:
            return False
        self.state["sent"][key] = self.now().isoformat()
        return True

    def tick(self) -> None:
        self.refresh()
        if not self.state["club"]:
            return
        now = self.now()
        m = self.next_match()
        changed = False
        if m:
            mins = (m.when - now).total_seconds() / 60
            if 0 < mins <= self.state["remind_min"] and self._once(f"remind:{m.id}"):
                changed = True
                text = f"{m.title()} через {int(round(mins))} мин" + (f" — {m.league}" if m.league else "")
                self.notify("МАТЧ", text)
                self.say(f"[СИСТЕМА: коротко напомни пользователю, что скоро матч его клуба: {text}.]")
            if m.when - LIVE_BEFORE <= now <= m.when + LIVE_AFTER:
                changed |= self._follow(m)
            else:
                self.current = None
        else:
            self.current = None
        if self.state["news"] and time.time() - float(self.state["news_at"]) > NEWS_EVERY:
            self.state["news_at"] = time.time()
            changed = True
            for n in self.news(important_only=True, limit=3):
                key = _norm(n.title)[:80]
                if key in self.state["seen_news"]:
                    continue
                self.state["seen_news"].append(key)
                self.notify("НОВОСТЬ КЛУБА", n.title)
        if changed:
            self.save()

    def _follow(self, m: Match) -> bool:
        live = self.live(m)
        self.current = live if live.state == "in" else None
        score = f"{live.home_score}:{live.away_score}"
        prev = self.state["scores"].get(m.id)
        changed = False
        if live.state == "in" and self._once(f"kickoff:{m.id}"):
            self.notify("МАТЧ НАЧАЛСЯ", live.title())
            changed = True
        if live.state in ("in", "post") and live.home_score != "" and score != prev:
            self.state["scores"][m.id] = score
            changed = True
            if prev is not None and self.state["goals"] and live.state == "in":
                self.notify("ГОЛ", f"{live.score()} ({live.detail})")
                self.say(f"[СИСТЕМА: гол в матче клуба пользователя! Счёт {live.score()} ({live.detail}). "
                         "Скажи одной живой фразой.]")
        if live.state == "post" and self._once(f"final:{m.id}"):
            changed = True
            self.notify("ИТОГ", live.score())
            self.say(f"[СИСТЕМА: матч клуба пользователя закончился: {live.score()}. Скажи итог одной фразой.]")
            self._fixtures_at = 0.0                     # обновить расписание
        return changed

    def start(self, every: float = 60.0):
        def loop():
            while not self._stop.wait(every):
                try:
                    self.tick()
                except Exception as exc:
                    logger.debug("Футбол: %s", exc)
        threading.Thread(target=loop, daemon=True, name="football").start()


_fb: Football | None = None


def football() -> Football:
    global _fb
    if _fb is None:
        env = os.getenv("JARVIS_FOOTBALL", "").strip()
        if env:
            path = Path(env)
        else:
            from core.paths import get_data_root
            path = Path(get_data_root()) / "football.json"
        _fb = Football(path)
    return _fb


def football_tool(p: dict) -> str:
    p = p or {}
    a = str(p.get("action") or "next").lower()
    fb = football()
    if a == "set_club":
        from core import about_me
        club = str(p.get("club") or "")
        about_me.answer("club", club, sync_now=False)             # и в анкету «Обо мне»
        return fb.set_club(club)
    if a == "next":
        return fb.next_text()
    if a in ("last", "result"):
        return fb.last_text()
    if a == "score":
        m = fb.next_match()
        if m and m.when - LIVE_BEFORE <= fb.now():
            m = fb.live(m)
            if m.state == "in":
                return f"Сейчас {m.score()} ({m.detail})."
        return fb.last_text() + " " + fb.next_text()
    if a == "news":
        return fb.news_text()
    if a == "watch":
        return watch_match(str(p.get("match") or ""))
    if a in ("goals_off", "goals_on", "news_off", "news_on"):
        what, on = a.split("_")
        fb.state[what] = on == "on"
        fb.save()
        return {"goals": "О голах", "news": "О новостях клуба"}[what] + (" буду сообщать." if on == "on"
                                                                       else " сообщать не буду.")
    return f"Не понял действие «{a}»."


# ── включить матч на Кинопоиске ──────────────────────────────────────────────
# Законная трансляция по подписке: hd.kinopoisk.ru/sport. Страница — живое
# приложение, вёрстка меняется, поэтому ищем не по классам, а по смыслу: на
# странице карточка-ссылка, в тексте которой есть обе команды (по-русски в
# любом падеже или по-английски). Нашли — открываем, жмём «Смотреть»,
# разворачиваем. Нет матча — честно говорим (на Кинопоиске идут не все игры).

KINOPOISK_SPORT = ["https://hd.kinopoisk.ru/sport/", "https://hd.kinopoisk.ru/sport/competition/37526/"]
_LINKS_JS = r"""JSON.stringify([...document.querySelectorAll('a[href]')].map(a => ({href: a.href,
  text: [a.innerText, a.getAttribute('aria-label'), a.title,
         ...[...a.querySelectorAll('img[alt]')].map(i => i.alt)].filter(Boolean).join(' ')
         .replace(/\s+/g, ' ').trim().slice(0, 300)})).filter(x => x.text))"""
_PLAY_JS = r"""(() => { const want = /смотреть|трансляц|продолжить|начать просмотр|watch|play/i;
  const els = [...document.querySelectorAll('button, a, [role=button]')].filter(e =>
    want.test((e.innerText || '') + ' ' + (e.getAttribute('aria-label') || '')) && e.offsetParent);
  if (els.length) { els[0].click(); return 'button'; }
  const v = document.querySelector('video'); if (v) { v.play(); return 'video'; }
  return ''; })()"""


def match_teams(query: str) -> list[tuple[str, str]]:
    """«матч Реала против Барсы» → [(Real Madrid, Реал Мадрид), (Barcelona, Барселона)]."""
    q = re.sub(r"^(поставь|включи|покажи|открой)?\s*(матч|игру)?\s*", "", (query or "").strip(), flags=re.I)
    parts = [p for p in re.split(r"\s+(?:против|vs\.?|v|—|–|-|и|с)\s+", q, flags=re.I) if p.strip()]
    return [club_names(p) for p in parts[:2]]


def _stems(team: tuple[str, str]) -> list[str]:
    eng, ru = team
    words = [w for w in _norm(ru).split() if len(w) > 2]
    return [w[:max(4, len(w) - 2)] for w in words[:1]] + ([_norm(eng)] if eng else [])


def pick_match(links: list[dict], teams: list[tuple[str, str]]) -> str | None:
    """Ссылка на матч, где есть все названные команды. Страницы турниров — ниже матчей."""
    best, best_rank = None, None
    for i, ln in enumerate(links):
        text = _norm(ln.get("text", ""))
        hits = sum(any(s and s in text for s in _stems(t)) for t in teams)
        if not teams or hits < len(teams):
            continue
        href = ln.get("href", "")
        rank = ("/competition/" in href, i)
        if best_rank is None or rank < best_rank:
            best, best_rank = href, rank
    return best


def watch_match(query: str, tab=None, pages: list[str] | None = None, wait=time.sleep) -> str:
    """Открыть матч на Кинопоиске в браузере Джарвиса."""
    teams = match_teams(query)
    if not teams:
        return "Какой матч включить? Скажите, например: «Реал против Барселоны»."
    from core import browser_cdp as cdp
    if tab is None:
        if not cdp.ensure_browser():
            return "Не получилось открыть браузер."
        tab = cdp.tab()
    names = " — ".join(t[1] for t in teams)
    login = False
    for page in pages or KINOPOISK_SPORT:
        tab.navigate(page)
        tab.wait("document.readyState === 'complete'", timeout=20)
        for _ in range(5):                                  # карточки подгружаются при прокрутке
            wait(0.8)
            tab.eval("window.scrollBy(0, innerHeight * 2)")
        try:
            links = json.loads(tab.eval(_LINKS_JS) or "[]")
        except ValueError:
            links = []
        href = pick_match(links, teams)
        if href:
            tab.navigate(href)
            tab.wait("document.readyState === 'complete'", timeout=20)
            wait(2.0)
            how = tab.eval(_PLAY_JS) or ""
            try:
                cdp.bring_to_front()
                cdp.fullscreen(True, t=tab)
            except Exception as exc:
                logger.debug("Матч, весь экран: %s", exc)
            return (f"Включил {names} на Кинопоиске." if how else
                    f"Открыл страницу матча {names} на Кинопоиске — нажмите «Смотреть», если не началось.")
        login = login or bool(tab.eval("/Войти/.test(document.body.innerText) && "
                                       "!/Профиль|Выйти/.test(document.body.innerText)"))
    hint = (" Похоже, в браузере Джарвиса не выполнен вход в Кинопоиск — войдите один раз, "
            "дальше он запомнит." if login else "")
    return f"На Кинопоиске матча {names} не нашёл — там показывают не все игры.{hint}"

