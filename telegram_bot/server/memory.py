"""Persistent memory — SQLite. Stores messages, facts, user profile."""
import json
from datetime import datetime
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "jarvis_memory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    role      TEXT    NOT NULL,
    content   TEXT    NOT NULL,
    ts        TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    fact      TEXT    NOT NULL,
    ts        TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS user_profile (
    user_id     INTEGER PRIMARY KEY,
    name        TEXT,
    preferences TEXT DEFAULT '{}',
    goals       TEXT DEFAULT '[]',
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id);
CREATE INDEX IF NOT EXISTS idx_fact_user ON facts(user_id);
"""


class Memory:
    def __init__(self, user_id: int):
        self._uid = user_id
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        self._db = await aiosqlite.connect(DB_PATH)
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def add_message(self, role: str, content: str):
        await self._db.execute(
            "INSERT INTO messages(user_id,role,content,ts) VALUES(?,?,?,?)",
            (self._uid, role, content, datetime.now().isoformat()),
        )
        await self._db.commit()

    async def save_fact(self, fact: str):
        await self._db.execute(
            "INSERT INTO facts(user_id,fact,ts) VALUES(?,?,?)",
            (self._uid, fact, datetime.now().isoformat()),
        )
        await self._db.commit()

    async def get_context(self, msg_limit: int = 20) -> str:
        parts: list[str] = []

        # Profile
        async with self._db.execute(
            "SELECT name, preferences, goals FROM user_profile WHERE user_id=?", (self._uid,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            name, prefs_raw, goals_raw = row
            if name:
                parts.append(f"Имя пользователя: {name}")
            prefs = json.loads(prefs_raw or "{}")
            if prefs:
                parts.append("Предпочтения: " + json.dumps(prefs, ensure_ascii=False))
            goals = json.loads(goals_raw or "[]")
            if goals:
                parts.append("Цели: " + "; ".join(goals))

        # Facts
        async with self._db.execute(
            "SELECT fact FROM facts WHERE user_id=? ORDER BY ts DESC LIMIT 15", (self._uid,)
        ) as cur:
            rows = await cur.fetchall()
        if rows:
            parts.append("Что я знаю о пользователе:\n" + "\n".join(f"• {r[0]}" for r in rows))

        # Recent messages
        async with self._db.execute(
            "SELECT role, content FROM messages WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (self._uid, msg_limit),
        ) as cur:
            rows = await cur.fetchall()
        if rows:
            history = "\n".join(f"{r[0]}: {r[1]}" for r in reversed(rows))
            parts.append(f"История последних сообщений:\n{history}")

        return "\n\n".join(parts) if parts else "Первая сессия с этим пользователем."

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None
