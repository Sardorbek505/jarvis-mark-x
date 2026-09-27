"""Снимок данных ПК для телефона: учёба, «Обо мне», последние звонки, футбол.

Mini App живёт на сервере бота, а эти данные — на ПК. Раз в минуту ПК и
так ходит на сервер (memory/shared.py) — снимок едет с той же
синхронизацией, но только когда что-то поменялось. Сервер хранит его
(telegram_bot/pc_views.py), и телефон видит расписание и звонки даже при
выключенном компьютере.

Правки с телефона (задача, «сделано», ответ анкеты) идут на ПК через
pc_server (apply) и сразу возвращают свежую часть снимка.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime

logger = logging.getLogger(__name__)

PARTS = ("study", "about", "calls", "football")
_sent_hash: dict[str, str] = {}


def part(name: str) -> dict:
    if name == "study":
        from core.study import study
        s = study()
        s.refresh()
        return {"semester_start": s.semester_start, "lessons": [asdict(x) for x in s.lessons],
                "tasks": [asdict(t) for t in s.tasks if not t.done or t.id in _recent_done(s)]}
    if name == "about":
        from core import about_me as A
        have = A.answers()
        return {"groups": A.GROUPS, "questions": [
            {"key": q.key, "label": q.label, "group": q.group, "why": q.why, "placeholder": q.placeholder,
             "value": have.get(q.key, "")} for q in A.QUESTIONS]}
    if name == "calls":
        from core.call_log import call_log
        log = call_log()
        log.load()
        return {"calls": log.recent(10)}
    if name == "football":
        from core.football import football
        fb = football()
        fb.refresh()
        return fb.phone_snapshot()
    raise KeyError(name)


def _recent_done(s) -> set[str]:
    """Сделанные — только последние 10: на телефоне видно, что отметка прошла."""
    return {t.id for t in [t for t in s.tasks if t.done][-10:]}


def collect(only_changed: bool = True) -> dict:
    """{часть: данные} — по умолчанию только то, что изменилось с прошлой отправки."""
    out = {}
    for name in PARTS:
        try:
            data = part(name)
        except Exception as exc:
            logger.debug("Снимок для телефона, %s: %s", name, exc)
            continue
        h = hashlib.sha1(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if only_changed and _sent_hash.get(name) == h:
            continue
        data["at"] = datetime.now().isoformat(timespec="seconds")
        out[name] = (h, data)
    return out


def mark_sent(sent: dict):
    for name, (h, _d) in sent.items():
        _sent_hash[name] = h


def apply(action: str, msg: dict) -> dict:
    """Правка с телефона. → {"ok", "text", "data": {"part": имя, "snapshot": данные}}."""
    if action in ("study_add", "study_done"):
        from core.study import study
        s = study()
        s.refresh()
        if action == "study_add":
            title = str(msg.get("title") or "").strip()
            if not title:
                return {"ok": False, "text": "Пустая задача."}
            t = s.add_task(title, str(msg.get("subject") or ""), str(msg.get("due") or ""))
            text = "Записал: " + s.task_text(t)
        else:
            t = next((x for x in s.tasks if x.id == str(msg.get("id"))), None)
            if not t:
                return {"ok": False, "text": "Такой задачи уже нет."}
            t.done = not t.done
            s.save()
            text = ("Отметил: " if t.done else "Вернул: ") + s.task_text(t)
        name = "study"
    elif action == "about_answer":
        from core import about_me as A
        key = str(msg.get("key") or "")
        if key not in A.BY_KEY:
            return {"ok": False, "text": "Нет такого вопроса."}
        text = A.answer(key, str(msg.get("value") or ""))
        name = "about"
    elif action == "football_watch":
        # «Смотреть на ПК» с телефона — тот же поиск на Кинопоиске, что голосом.
        from core import football as F
        nxt = F.football().next_match()
        if not nxt:
            return {"ok": False, "text": "Нет матча, который можно включить."}
        return {"ok": True, "text": F.watch_match(f"{nxt.home} против {nxt.away}")}
    else:
        return {"ok": False, "text": f"Не знаю действие {action}."}
    snap = part(name)
    snap["at"] = datetime.now().isoformat(timespec="seconds")
    return {"ok": True, "text": text, "data": {"part": name, "snapshot": snap}}
