"""Одна память у голосового Джарвиса и Telegram-бота (сторона ПК).

Главная копия — на сервере бота (telegram_bot/shared_memory.py): он онлайн
всегда, даже когда компьютер выключен. Здесь:
  • очередь на отправку (sync_outbox.json): новые и устаревшие факты,
    «забудь про…», реплики голосом, итоги разговоров. Переживает
    отключение сети и перезапуск — ничего не теряется;
  • раз в минуту POST /api/memory/sync с тем же pc_link_token, что у
    /pc-link; ответ (все факты бота, профиль, новая переписка в Telegram)
    кладётся в shared.json и идёт в промпт.

Не настроено (нет pc_link_url/pc_link_token) — всё работает как раньше,
только локально.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

from core.paths import get_data_root

logger = logging.getLogger(__name__)

_DIR = get_data_root() / "memory"
OUTBOX_FILE = _DIR / "sync_outbox.json"
SHARED_FILE = _DIR / "shared.json"
SYNC_SEC = 60
TELEGRAM_IN_PROMPT = 10
TELEGRAM_MAX_AGE_H = 24
SHARED_CHARS = 3000

_lock = threading.Lock()
_KINDS = ("facts_add", "facts_remove", "forget", "turns", "episodes")


def fact_text(key: str, value: str) -> str:
    """Факт с ПК в виде строки бота: «брат: Азиз, учится в Ташкенте»."""
    return f"{str(key).replace('_', ' ').strip()}: {str(value).strip()}"


# ── очередь ───────────────────────────────────────────────────────────────────

def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _enqueue(kind: str, item):
    if not configured():
        return
    with _lock:
        box = _read(OUTBOX_FILE, {})
        items = box.setdefault(kind, [])
        if item not in items:
            items.append(item)
        del items[:-2000]
        _write(OUTBOX_FILE, box)


def queue_fact(key: str, value: str):
    _enqueue("facts_add", fact_text(key, value))


def queue_fact_removed(key: str, value: str):
    _enqueue("facts_remove", fact_text(key, value))


def queue_forget(query: str):
    _enqueue("forget", query.strip())


def queue_turn(role: str, text: str, ts: float):
    _enqueue("turns", {"role": role, "text": text, "ts": ts})


def queue_episode(text: str, ts: float):
    _enqueue("episodes", {"text": text, "ts": ts})


# ── связь с сервером ──────────────────────────────────────────────────────────

def _config() -> tuple[str, str]:
    try:
        from core.paths import load_api_keys
        k = load_api_keys()
    except Exception:
        k = {}
    url = os.getenv("PC_LINK_URL") or k.get("pc_link_url") or k.get("miniapp_url") or ""
    token = os.getenv("PC_LINK_TOKEN") or k.get("pc_link_token") or ""
    url = url.strip().rstrip("/")
    for a, b in (("wss://", "https://"), ("ws://", "http://")):
        if url.startswith(a):
            url = b + url[len(a):]
    return url, token.strip()


def configured() -> bool:
    url, token = _config()
    return bool(url and token)


def sync(post=None) -> str:
    """Один обмен с сервером. post(url, json, headers) → (status, dict) — для тестов."""
    url, token = _config()
    if not (url and token):
        return "не настроено"
    with _lock:
        box = _read(OUTBOX_FILE, {})
    shared = _read(SHARED_FILE, {})
    body = {k: box.get(k, []) for k in _KINDS}
    body["since_msg_id"] = shared.get("last_msg_id", 0)
    sent = {k: len(body[k]) for k in _KINDS}
    try:
        status, data = (post or _post)(url + "/api/memory/sync", body,
                                       {"Authorization": f"Bearer {token}"})
    except Exception as exc:
        logger.debug("Общая память: сервер недоступен: %s", exc)
        return f"нет связи: {exc}"
    if status != 200 or not isinstance(data, dict):
        logger.warning("Общая память: сервер ответил %s", status)
        return f"ответ {status}"
    with _lock:                                  # убрать отправленное, не трогая новое
        box = _read(OUTBOX_FILE, {})
        for k in _KINDS:
            box[k] = box.get(k, [])[sent[k]:]
        _write(OUTBOX_FILE, box)
    telegram = (shared.get("telegram", []) + (data.get("telegram") or []))[-40:]
    _write(SHARED_FILE, {"facts": data.get("facts") or [], "profile": data.get("profile") or {},
                         "telegram": telegram, "last_msg_id": data.get("last_msg_id", body["since_msg_id"]),
                         "synced_at": time.time()})
    return f"ок: +{data.get('added', 0)} −{data.get('removed', 0)}, фактов на сервере {len(data.get('facts') or [])}"


def _post(url: str, body: dict, headers: dict):
    import requests
    r = requests.post(url, json=body, headers=headers, timeout=(5, 20))
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def start():
    if not configured():
        logger.info("Общая память с ботом не настроена (нет pc_link_url/pc_link_token) — память только на ПК")
        return

    def loop():
        while True:
            try:
                res = sync()
                logger.debug("Общая память: %s", res)
            except Exception as exc:
                logger.warning("Общая память: %s", exc)
            time.sleep(SYNC_SEC)
    threading.Thread(target=loop, daemon=True, name="memory-sync").start()


# ── для промпта и поиска ──────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return " ".join(s.lower().replace("_", " ").split())


def shared_facts_not_local(local: list[tuple[str, str, str]]) -> list[str]:
    """Факты с сервера, которых нет в локальной памяти (свои не дублируем)."""
    mine = {_norm(fact_text(k, v)) for _, k, v in local}
    out = []
    for f in _read(SHARED_FILE, {}).get("facts", []):
        n = _norm(f)
        if n not in mine and not any(n in m or m in n for m in mine if len(m) > 8):
            out.append(f)
    return out


def _at(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def prompt_block(local: list[tuple[str, str, str]], now: datetime | None = None) -> str:
    shared = _read(SHARED_FILE, {})
    if not shared:
        return ""
    now = now or datetime.now()
    parts = []
    facts = shared_facts_not_local(local)
    if facts:
        text, size = [], 0
        for f in reversed(facts):                 # новые важнее
            if size + len(f) > SHARED_CHARS:
                break
            text.append(f)
            size += len(f) + 2
        parts.append("[ОБЩАЯ ПАМЯТЬ С TELEGRAM — это тоже ты знаешь о пользователе]\n  " + "\n  ".join(reversed(text)))
    # Часы сервера (UTC) и ПК расходятся на часовой пояс — берём с запасом.
    recent = [m for m in shared.get("telegram", [])
              if (_at(m.get("at", "")) or now) >= now - timedelta(hours=TELEGRAM_MAX_AGE_H + 14)]
    if recent:
        lines = [("Вы: " if m["role"] == "user" else "Джарвис: ") + m["text"][:300] for m in recent[-TELEGRAM_IN_PROMPT:]]
        parts.append("[НЕДАВНО В TELEGRAM — ты (Джарвис-бот) переписывался с пользователем]\n  " + "\n  ".join(lines))
    return "\n".join(parts) + ("\n" if parts else "")


def search(query: str, limit: int = 10) -> list[str]:
    from rapidfuzz import fuzz
    q = (query or "").strip().lower()
    shared = _read(SHARED_FILE, {})
    pool = list(shared.get("facts", [])) + [
        ("Вы в Telegram: " if m["role"] == "user" else "Бот в Telegram: ") + m["text"]
        for m in shared.get("telegram", [])]
    if not q:
        return list(shared.get("facts", []))[:limit]
    scored = [(max(fuzz.partial_ratio(q, t.lower()), fuzz.token_set_ratio(q, t.lower())), t) for t in pool]
    return [t for s, t in sorted(scored, key=lambda x: -x[0]) if s >= 65][:limit]
