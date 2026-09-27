"""Данные ПК в Mini App: учёба, «Обо мне», звонки, футбол, «Что умею» (сторона сервера).

ПК присылает снимок при синхронизации памяти (core/pc_snapshot.py →
shared_memory.apply_sync); здесь он хранится в meta (pc_snap_<часть>) и
превращается в то, что рисует телефон. Расписание считается тем же кодом,
что на ПК (core/study.py: чётные недели, «сегодня/завтра»), — поэтому
ответы одинаковые, а телефон видит их и при выключенном компьютере.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

PARTS = ("study", "about", "calls", "football")


async def store_snapshots(store, uid: int, snaps) -> int:
    if not isinstance(snaps, dict):
        return 0
    n = 0
    for name in PARTS:
        data = snaps.get(name)
        if isinstance(data, dict):
            await store.set_meta(uid, f"pc_snap_{name}", json.dumps(data, ensure_ascii=False)[:400_000])
            n += 1
    return n


async def load(store, uid: int, name: str) -> dict | None:
    raw = await store.get_meta(uid, f"pc_snap_{name}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def _updated(snap: dict) -> str:
    return str(snap.get("at") or "").replace("T", " ")[:16]


def study_view(snap: dict | None, now: datetime) -> dict:
    """Неделя с парами, «сейчас / следующая», задачи по срокам."""
    if not snap:
        return {"has": False}
    from core import study as S
    st = S.Study.from_data(snap, now=lambda: now)
    today = now.date()
    monday = today - timedelta(days=today.weekday())
    week = []
    for i in range(7):
        d = monday + timedelta(days=i)
        week.append({"day": S.WEEKDAYS_SHORT[i], "date": f"{d:%d.%m}", "today": d == today,
                     "lessons": [{"start": x.start, "end": x.end, "subject": x.subject, "room": x.room,
                                  "kind": x.kind if x.kind != "лекция" else "", "teacher": x.teacher}
                                 for x in st.lessons_on(d)]})
    tasks = []
    for t in sorted(st.tasks, key=lambda t: (t.done, t.due or "9999", t.title)):
        due = date.fromisoformat(t.due) if t.due else None
        if t.done:
            group = "done"
        elif not due:
            group = "nodate"
        elif due < today:
            group = "overdue"
        elif due == today:
            group = "today"
        elif due == today + timedelta(days=1):
            group = "tomorrow"
        elif due <= today + timedelta(days=7):
            group = "week"
        else:
            group = "later"
        tasks.append({"id": t.id, "title": t.title, "subject": t.subject, "kind": t.kind, "done": t.done,
                      "due": S.say_date(due, today) if due else "", "group": group})
    return {"has": True, "updated": _updated(snap), "now_next": st.now_next() if st.lessons else "",
            "parity": "нечётная" if st.week_parity(today) == "odd" else "чётная",
            "has_lessons": bool(st.lessons), "week": week, "tasks": tasks}


def about_view(snap: dict | None) -> dict:
    if not snap:
        return {"has": False}
    qs = [q for q in snap.get("questions", []) if isinstance(q, dict) and q.get("key")]
    groups = []
    for g in snap.get("groups") or []:
        items = [q for q in qs if q.get("group") == g]
        if items:
            groups.append({"title": g, "questions": items})
    known = sum(1 for q in qs if q.get("value"))
    return {"has": True, "updated": _updated(snap), "known": known, "total": len(qs), "groups": groups}


def calls_view(snap: dict | None) -> dict:
    if not snap:
        return {"has": False, "calls": []}
    out = []
    for c in snap.get("calls") or []:
        if not isinstance(c, dict):
            continue
        out.append({"id": c.get("id", ""), "who": "вам" if c.get("who") == "вам" else c.get("who", ""),
                    "when": str(c.get("when", "")).replace("T", " ")[:16], "result": c.get("result", ""),
                    "min": max(1, round(int(c.get("sec") or 0) / 60)),
                    "lines": [ln for ln in c.get("lines") or [] if isinstance(ln, dict)][:200]})
    return {"has": True, "calls": out}


def _letter(m: dict, team: str) -> str:
    hs, as_ = str(m.get("hs", "")), str(m.get("as", ""))
    if not (hs.isdigit() and as_.isdigit()):
        return ""
    ours, theirs = (int(as_), int(hs)) if team and m.get("away") == team else (int(hs), int(as_))
    return "В" if ours > theirs else "Н" if ours == theirs else "П"


def football_view(snap: dict | None) -> dict:
    """Карточка клуба: матч (идёт / ближайший), форма В/Н/П, ближайшие, новости."""
    if not snap or not snap.get("club"):
        return {"has": False}
    team = snap.get("team", "")
    results = [dict(m, res=_letter(m, team)) for m in snap.get("results") or [] if isinstance(m, dict)]
    return {"has": True, "updated": _updated(snap), "name": snap.get("name") or snap.get("club"), "team": team,
            "next": snap.get("next") if isinstance(snap.get("next"), dict) else None, "results": results,
            "upcoming": [m for m in snap.get("upcoming") or [] if isinstance(m, dict)],
            "news": [n for n in snap.get("news") or [] if isinstance(n, dict) and n.get("title")][:4]}


def abilities() -> list[dict]:
    """«Что умею» — те же разделы и фразы, что в окне на ПК (core/help.py)."""
    try:
        from core.help import ABILITIES
        return [{"title": t, "phrases": list(p)} for t, _icon, p in ABILITIES]
    except Exception as exc:
        logger.debug("Что умею: %s", exc)
        return []
