"""Одна память у Telegram-бота и голосового Джарвиса на ПК (серверная сторона).

Главная копия — здесь, в базе бота: она доступна всегда, даже когда ПК
выключен. ПК раз в минуту шлёт POST /api/memory/sync:
  facts_add       — новые факты («брат: Азиз, учится в Ташкенте»)
  facts_remove    — устаревшие факты (точный текст)
  forget          — «забудь про X»: убрать все факты, где есть X
  turns           — реплики голосом [{role: user|jarvis, text, ts}]
  episodes        — итоги разговоров на ПК [{text, ts}]
  since_msg_id    — последнее сообщение Telegram, которое ПК уже видел
  ops             — новый формат: факты по порядку [{op: add|remove|forget, text}]
Ответ: applied — сколько взято из каждой очереди (ПК убирает у себя только их).
и получает все факты, профиль и новую переписку из Telegram.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_MAX_ITEMS = 300


def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""


async def _apply_ops(store, uid: int, ops: list) -> tuple[int, int, int]:
    """Упорядоченные операции с фактами: [{op: add|remove|forget, text}].
    Порядок важен: «запомни X» → «забудь X» должно кончиться забытым X.
    Раньше удаления применялись до добавлений, и забытый факт возвращался."""
    added = removed = applied = 0
    for o in ops[:_MAX_ITEMS]:
        applied += 1
        if not isinstance(o, dict) or not isinstance(o.get("text"), str):
            continue
        text, op = o["text"].strip(), o.get("op")
        if not text:
            continue
        if op == "add" and await store.add_fact(uid, text[:400]):
            added += 1
        elif op == "remove" and await store.del_fact_text(uid, text):
            removed += 1
        elif op == "forget":
            removed += len(await store.forget_like(uid, text))
    return added, removed, applied


async def apply_sync(store, uid: int, body: dict) -> dict:
    body = body or {}
    added = removed = 0
    ops = body.get("ops") if isinstance(body.get("ops"), list) else []
    a, r, ops_applied = await _apply_ops(store, uid, ops)
    added, removed = added + a, removed + r
    for fact in (body.get("facts_remove") or [])[:_MAX_ITEMS]:
        if isinstance(fact, str) and await store.del_fact_text(uid, fact):
            removed += 1
    for query in (body.get("forget") or [])[:50]:
        if isinstance(query, str):
            removed += len(await store.forget_like(uid, query))
    for fact in (body.get("facts_add") or [])[:_MAX_ITEMS]:
        if isinstance(fact, str) and fact.strip() and await store.add_fact(uid, fact.strip()[:400]):
            added += 1
    for t in (body.get("turns") or [])[:_MAX_ITEMS]:
        if isinstance(t, dict) and t.get("role") in ("user", "jarvis"):
            await store.add_pc_message(uid, "pc_user" if t["role"] == "user" else "pc_jarvis",
                                       str(t.get("text", "")), _iso(t.get("ts")))
    for e in (body.get("episodes") or [])[:50]:
        if isinstance(e, dict):
            await store.add_pc_message(uid, "pc_episode", str(e.get("text", "")), _iso(e.get("ts")))
    if added or removed:
        logger.info("Общая память: с ПК +%d фактов, −%d", added, removed)
    if body.get("snapshots"):
        from telegram_bot import pc_views
        n = await pc_views.store_snapshots(store, uid, body["snapshots"])
        logger.info("Снимок ПК для телефона: частей %d", n)

    since = int(body.get("since_msg_id") or 0)
    msgs = await store.messages_after(uid, since, 30)
    return {
        "facts": await store.get_facts(uid),
        "profile": await store.get_profile(uid),
        "telegram": [{"role": m["role"], "text": m["text"], "at": m["created_at"]} for m in msgs],
        "last_msg_id": msgs[-1]["id"] if msgs else since,
        "added": added, "removed": removed,
        # Сколько взял из каждой очереди: ПК удаляет у себя ровно это, а не всё
        # отправленное (раньше сверх 300 пропадало молча). ops — новый формат.
        "ops_ok": True,
        "applied": {"ops": ops_applied, "facts_add": min(len(body.get("facts_add") or []), _MAX_ITEMS),
                    "facts_remove": min(len(body.get("facts_remove") or []), _MAX_ITEMS),
                    "forget": min(len(body.get("forget") or []), 50),
                    "turns": min(len(body.get("turns") or []), _MAX_ITEMS),
                    "episodes": min(len(body.get("episodes") or []), 50)},
    }


def voice_block(store, uid: int) -> str:
    """Для контекста бота: о чём недавно говорили голосом на ПК."""
    voice = store.cached_voice(uid)
    if not voice:
        return ""
    episodes = [v["text"] for v in voice if v["role"] == "pc_episode"][-3:]
    lines = [("Вы: " if v["role"] == "pc_user" else "Джарвис: ") + v["text"][:300]
             for v in voice if v["role"] != "pc_episode"][-8:]
    parts = []
    if episodes:
        parts.append("Итоги недавних разговоров голосом на ПК: " + " | ".join(episodes))
    if lines:
        parts.append("Последнее, что говорили голосом на ПК (голосовой Джарвис на компьютере — это тоже ты, "
                     "у вас одна память): " + " / ".join(lines))
    return "\n".join(parts)
