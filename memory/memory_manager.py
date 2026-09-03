"""
ДЖАРВИС — Менеджер долгосрочной памяти (Единая память ПК и Telegram).

Синхронизирует память между:
- memory/data.json (локальный кэш ПК)
- config/jarvis_memory.db (единая база SQLite для Telegram и ПК: факты, профиль, заметки, задачи)
- config/user_profile.json (профиль предпочтений)
"""

import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("jarvis-memory-sync")

_BASE = Path(__file__).resolve().parent.parent
_MEMORY_FILE = _BASE / "memory" / "data.json"
_DB_FILE = _BASE / "config" / "jarvis_memory.db"
_PROFILE_FILE = _BASE / "config" / "user_profile.json"
_API_KEYS_FILE = _BASE / "config" / "api_keys.json"

# Гарантируем что core/ доступен
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core.storage import atomic_write_json, safe_read_json


def _get_owner_uid() -> int:
    """Получает Telegram ID владельца из config/api_keys.json."""
    try:
        if _API_KEYS_FILE.exists():
            with open(_API_KEYS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            raw = d.get("telegram_allowed_users")
            if isinstance(raw, list) and raw:
                return int(raw[0])
            if str(raw).isdigit():
                return int(raw)
    except Exception:
        pass
    return 1328351268


def load_memory() -> dict:
    """Загружает объединённую память: data.json + SQLite jarvis_memory.db + user_profile.json."""
    mem = safe_read_json(_MEMORY_FILE, default={})
    uid = _get_owner_uid()

    # 1. Читаем user_profile.json
    try:
        if _PROFILE_FILE.exists():
            with open(_PROFILE_FILE, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            ident = pdata.get("identity", {})
            if ident.get("name") or ident.get("creator"):
                mem.setdefault("identity", {})
                if ident.get("name"):
                    mem["identity"]["name"] = ident["name"]
                if ident.get("creator"):
                    mem["identity"]["creator"] = ident["creator"]

            pref = pdata.get("preferences", {})
            if pref:
                mem.setdefault("preferences", {})
                for k, v in pref.items():
                    if v:
                        mem["preferences"][k] = v

            hist = pdata.get("history", {})
            if hist.get("recent_music"):
                mem.setdefault("preferences", {})
                mem["preferences"]["recent_music"] = ", ".join(hist["recent_music"][:5])
    except Exception as e:
        logger.debug("Error loading user_profile.json: %s", e)

    # 2. Читаем SQLite jarvis_memory.db (Telegram facts, profile, notes, tasks)
    try:
        if _DB_FILE.exists():
            conn = sqlite3.connect(str(_DB_FILE))
            cur = conn.cursor()

            # Факты из Telegram
            cur.execute("SELECT fact FROM facts WHERE user_id = ? ORDER BY id DESC LIMIT 50", (uid,))
            db_facts = [r[0] for r in cur.fetchall()]
            if db_facts:
                mem.setdefault("facts_tg", {})
                for i, f in enumerate(db_facts):
                    mem["facts_tg"][f"fact_{i+1}"] = f

            # Заметки
            cur.execute("SELECT text FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 10", (uid,))
            db_notes = [r[0] for r in cur.fetchall()]
            if db_notes:
                mem.setdefault("notes", {})
                for i, n in enumerate(db_notes):
                    mem["notes"][f"note_{i+1}"] = n

            # Профиль из БД
            cur.execute("SELECT name, about, goals, preferences FROM profile WHERE user_id = ?", (uid,))
            row = cur.fetchone()
            if row:
                name, about, goals, prefs = row
                mem.setdefault("identity", {})
                if name:
                    mem["identity"]["name"] = name
                if about:
                    mem["identity"]["about"] = about
                if goals:
                    mem["identity"]["goals"] = goals
                if prefs:
                    mem.setdefault("preferences", {})["db_preferences"] = prefs

            # Задачи
            cur.execute("SELECT title, due FROM tasks WHERE user_id = ? AND done = 0 LIMIT 10", (uid,))
            db_tasks = cur.fetchall()
            if db_tasks:
                mem.setdefault("tasks", {})
                for i, (t_title, t_due) in enumerate(db_tasks):
                    mem["tasks"][f"task_{i+1}"] = f"{t_title}" + (f" (до {t_due})" if t_due else "")

            conn.close()
    except Exception as e:
        logger.debug("Error reading jarvis_memory.db: %s", e)

    return mem


def update_memory(patch: dict):
    """Обновляет память в memory/data.json и синхронизирует в jarvis_memory.db."""
    mem = load_memory()
    for category, items in patch.items():
        if category not in mem:
            mem[category] = {}
        for key, val in items.items():
            mem[category][key] = val
    atomic_write_json(_MEMORY_FILE, mem)

    # Синхронизируем в jarvis_memory.db
    uid = _get_owner_uid()
    try:
        if _DB_FILE.exists():
            conn = sqlite3.connect(str(_DB_FILE))
            cur = conn.cursor()
            now_iso = datetime.now().isoformat()

            for cat, items in patch.items():
                for key, val in items.items():
                    val_str = str(val.get("value", val) if isinstance(val, dict) else val).strip()
                    if not val_str:
                        continue
                    if cat in ("notes", "заметки"):
                        cur.execute(
                            "INSERT INTO notes (user_id, text, created_at) VALUES (?, ?, ?)",
                            (uid, val_str, now_iso),
                        )
                    else:
                        cur.execute(
                            "INSERT INTO facts (user_id, fact, ts) VALUES (?, ?, ?)",
                            (uid, f"{key}: {val_str}", now_iso),
                        )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.debug("Error updating jarvis_memory.db: %s", e)


def format_memory_for_prompt(memory: dict) -> str:
    """Форматирует объединённую память для системного промпта ПК-ассистента."""
    if not memory:
        return ""
    lines = ["[ЕДИНАЯ ПАМЯТЬ ДЖАРВИСА (ПК + TELEGRAM)]"]

    # Личность
    ident = memory.get("identity", {})
    if ident:
        lines.append("\nЛичность пользователя:")
        for k, v in ident.items():
            lines.append(f"  {k}: {v}")

    # Предпочтения
    pref = memory.get("preferences", {})
    if pref:
        lines.append("\nПредпочтения:")
        for k, v in pref.items():
            lines.append(f"  {k}: {v}")

    # Факты из Telegram
    facts = memory.get("facts_tg", {})
    if facts:
        lines.append("\nФакты, сохранённые из Telegram:")
        for _, f in list(facts.items())[:20]:
            lines.append(f"  • {f}")

    # Задачи
    tasks = memory.get("tasks", {})
    if tasks:
        lines.append("\nАктивные задачи:")
        for _, t in list(tasks.items())[:10]:
            lines.append(f"  • {t}")

    # Заметки
    notes = memory.get("notes", {})
    if notes:
        lines.append("\nНедавние заметки:")
        for _, n in list(notes.items())[:10]:
            lines.append(f"  • {n}")

    # Прочее
    for category, items in memory.items():
        if category in ("identity", "preferences", "facts_tg", "tasks", "notes"):
            continue
        lines.append(f"\n{category}:")
        for key, val in items.items():
            if isinstance(val, dict):
                lines.append(f"  {key}: {val.get('value', val)}")
            else:
                lines.append(f"  {key}: {val}")

    lines.append("")
    return "\n".join(lines)
