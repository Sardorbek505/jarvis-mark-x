"""Одна память у Telegram-бота и голосового Джарвиса на ПК (серверная сторона).

Главная копия — здесь, в базе бота: она доступна всегда, даже когда ПК
выключен. ПК раз в минуту шлёт POST /api/memory/sync:
  facts_add       — новые факты («брат: Азиз, учится в Ташкенте»)
  facts_remove    — устаревшие факты (точный текст)
  forget          — «забудь про X»: убрать все факты, где есть X
  turns           — реплики голосом [{role: user|jarvis, text, ts}]
  episodes        — итоги разговоров на ПК [{text, ts}]
  since_msg_id    — последнее сообщение Telegram, которое ПК уже видел
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


async def apply_sync(store, uid: int, body: dict) -> dict:
    body = body or {}
    added = removed = 0
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

    since = int(body.get("since_msg_id") or 0)
    msgs = await store.messages_after(uid, since, 30)
    return {
        "facts": await store.get_facts(uid),
        "profile": await store.get_profile(uid),
        "telegram": [{"role": m["role"], "text": m["text"], "at": m["created_at"]} for m in msgs],
        "last_msg_id": msgs[-1]["id"] if msgs else since,
        "added": added, "removed": removed,
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
