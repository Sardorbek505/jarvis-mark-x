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
_KINDS = ("ops", "facts_add", "facts_remove", "forget", "turns", "episodes")
# Сколько сервер берёт за раз (telegram_bot/shared_memory.py). Шлём не больше:
# раньше уходило до 2000, сервер брал 300, а ПК стирал у себя всё отправленное.
_LIMITS = {"ops": 300, "facts_add": 300, "facts_remove": 300, "forget": 50, "turns": 300, "episodes": 50}
_ROUNDS = 8                     # пачек за один обмен, пока очередь не опустеет
_MAX_LOCAL_DELETES = 0.2        # доля подтверждённых фактов, которую сервер может «удалить» за раз


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
        # Операции с фактами — по порядку и без «схлопывания»: «запомни X,
        # забудь X, запомни X» должно кончиться запомненным X. Подряд одинаковые — одна.
        if kind == "ops" and (not items or items[-1] != item) or kind != "ops" and item not in items:
            items.append(item)
        del items[:-5000]
        _write(OUTBOX_FILE, box)


def queue_fact(key: str, value: str):
    _enqueue("ops", {"op": "add", "text": fact_text(key, value)})


def queue_fact_removed(key: str, value: str):
    _enqueue("ops", {"op": "remove", "text": fact_text(key, value)})


def queue_forget(query: str):
    _enqueue("ops", {"op": "forget", "text": query.strip()})


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


def _drop_sent(items: list, sent: list, n: int) -> list:
    """Убрать из очереди первые n отправленных. Очередь могла за это время
    подрезаться сверху — тогда убираем по совпадению, а не по номеру."""
    if items[:n] == sent[:n]:
        return items[n:]
    rest = list(items)
    for it in sent[:n]:
        if it in rest:
            rest.remove(it)
    return rest


def _bootstrap(shared: dict):
    """Первая связь: всё, что ПК узнал ДО подключения к боту, — на сервер.
    Раньше очередь не велась, пока связь не настроена, и это терялось."""
    if shared.get("bootstrapped"):
        return
    try:
        from memory.memory_manager import all_facts
        facts = all_facts()
    except Exception as exc:
        logger.debug("Общая память: факты для первой выгрузки: %s", exc)
        return
    for _cat, key, value in facts:
        _enqueue("ops", {"op": "add", "text": fact_text(key, value)})
    shared["bootstrapped"] = True
    _write(SHARED_FILE, shared)
    logger.info("Общая память: первая выгрузка на сервер — фактов %d", len(facts))


def _apply_server_deletes(server_facts: list, shared: dict) -> list[str]:
    """Факт удалили с телефона/в боте — убрать и на ПК. Только факты, которые
    сервер раньше подтверждал (свои несинхронизированные не трогаем), и не
    больше доли за раз: пустая база на сервере (сбой, временное хранилище)
    не должна стереть память ПК."""
    try:
        from memory import memory_manager as mm
        local = mm.all_facts()
    except Exception:
        return []
    server = {_norm(f) for f in server_facts}
    confirmed = set(shared.get("confirmed") or [])
    with _lock:
        pending = {_norm(o.get("text", "")) for o in _read(OUTBOX_FILE, {}).get("ops", []) if isinstance(o, dict)}
    gone, now_confirmed = [], set()
    for cat, key, value in local:
        n = _norm(fact_text(key, value))
        if n in server:
            now_confirmed.add(n)
        elif n in confirmed and n not in pending:
            gone.append((cat, key, n))
    if server and gone and len(gone) <= max(2, int(len(confirmed) * _MAX_LOCAL_DELETES)):
        for cat, key, _n in gone:
            mm.forget(cat, key, share=False)
        logger.info("Общая память: удалено на ПК вслед за ботом — %d", len(gone))
    elif gone:
        logger.warning("Общая память: сервер не знает %d подтверждённых фактов — не удаляю (подозрительно много)",
                       len(gone))
        now_confirmed |= {n for _c, _k, n in gone}
    shared["confirmed"] = sorted(now_confirmed)
    return [n for _c, _k, n in gone]


def sync_all(post=None) -> str:
    """Обмен пачками, пока очередь не опустеет (не больше _ROUNDS за раз)."""
    res = ""
    for _ in range(_ROUNDS):
        res = sync(post)
        with _lock:
            box = _read(OUTBOX_FILE, {})
        if not res.startswith("ок") or not any(box.get(k) for k in _KINDS):
            break
    return res


def sync(post=None) -> str:
    """Один обмен с сервером. post(url, json, headers) → (status, dict) — для тестов."""
    url, token = _config()
    if not (url and token):
        return "не настроено"
    shared = _read(SHARED_FILE, {})
    _bootstrap(shared)                           # самый первый обмен — до любых удалений с сервера
    with _lock:
        box = _read(OUTBOX_FILE, {})
    own = {k: list(box.get(k, []))[:_LIMITS[k]] for k in _KINDS}
    body = {k: list(v) for k, v in own.items()}
    ops_sent = own["ops"]
    if not shared.get("server_ops"):
        # Сервер ещё не сказал, что понимает ops (или он старый) — те же
        # операции старыми очередями, по порядку и только пока есть место.
        body.pop("ops")
        name = {"add": "facts_add", "remove": "facts_remove", "forget": "forget"}
        taken = []
        for o in ops_sent:
            kind = name.get(o.get("op")) if isinstance(o, dict) else None
            if kind is None or len(body[kind]) >= _LIMITS[kind]:
                break
            body[kind].append(o.get("text", ""))
            taken.append(o)
        ops_sent = taken
    body["since_msg_id"] = shared.get("last_msg_id", 0)
    # Учёба, «Обо мне», звонки — для телефона (core/pc_snapshot.py); только изменившееся.
    snaps = {}
    try:
        from core import pc_snapshot
        snaps = pc_snapshot.collect()
        if snaps:
            body["snapshots"] = {name: data for name, (_h, data) in snaps.items()}
    except Exception as exc:
        logger.debug("Снимок для телефона: %s", exc)
    try:
        status, data = (post or _post)(url + "/api/memory/sync", body,
                                       {"Authorization": f"Bearer {token}"})
    except Exception as exc:
        logger.debug("Общая память: сервер недоступен: %s", exc)
        return f"нет связи: {exc}"
    if status != 200 or not isinstance(data, dict):
        logger.warning("Общая память: сервер ответил %s", status)
        return f"ответ {status}"
    if snaps:
        from core import pc_snapshot
        pc_snapshot.mark_sent(snaps)
    applied = data.get("applied") if isinstance(data.get("applied"), dict) else {}
    with _lock:                                  # убрать подтверждённое, не трогая новое
        box = _read(OUTBOX_FILE, {})
        for k in _KINDS:
            mine = ops_sent if k == "ops" else own[k]
            n = len(mine) if k == "ops" and "ops" not in body else min(len(mine), int(applied.get(k, len(mine))))
            box[k] = _drop_sent(box.get(k, []), mine, n)
        _write(OUTBOX_FILE, box)
    shared = _read(SHARED_FILE, {}) or shared
    telegram = (shared.get("telegram", []) + (data.get("telegram") or []))[-40:]
    shared.update({"facts": data.get("facts") or [], "profile": data.get("profile") or {},
                   "telegram": telegram, "last_msg_id": data.get("last_msg_id", body["since_msg_id"]),
                   "synced_at": time.time(), "server_ops": bool(data.get("ops_ok"))})
    if data.get("ops_ok"):
        _apply_server_deletes(data.get("facts") or [], shared)
    _write(SHARED_FILE, shared)
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
                res = sync_all()
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
