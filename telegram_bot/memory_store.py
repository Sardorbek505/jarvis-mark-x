"""
Durable long-term memory for JARVIS — the foundation for being a real
secretary / friend / advisor that remembers you across restarts.

Storage backends (chosen automatically):
  • Postgres  — if env DATABASE_URL is set (e.g. free Neon DB). Survives
    Render restarts/redeploys → true long-term memory.
  • SQLite    — fallback. Works locally and on the PC, but on Render's free
    tier the file is wiped on restart (use DATABASE_URL for real persistence).

Holds a per-user dossier (name / about / goals / preferences) and a list of
durable facts. Keeps an in-RAM cache so the system prompt can be built
synchronously while DB access stays async.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis-memory")

_SQLITE_PATH = Path(__file__).resolve().parent.parent / "config" / "jarvis_memory.db"

_MAX_FACTS = 60  # keep the newest N facts per user in context


class MemoryStore:
    def __init__(self):
        self._url = os.getenv("DATABASE_URL", "").strip()
        self._pg = self._url.startswith(("postgres://", "postgresql://"))
        self._pool = None          # asyncpg pool
        self._sqlite = None        # aiosqlite connection
        self._cache: dict = {}     # uid -> {"profile": {...}, "facts": [str]}

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def init(self):
        if self._pg:
            import asyncpg
            # Neon and most managed PGs require SSL
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=3)
            await self._exec_pg(_SCHEMA_PG)
            await self._exec_pg("ALTER TABLE profile ADD COLUMN IF NOT EXISTS mode TEXT DEFAULT ''")
            logger.info("Memory: Postgres connected (durable) ✅")
        else:
            import aiosqlite
            _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite = await aiosqlite.connect(_SQLITE_PATH)
            await self._sqlite.executescript(_SCHEMA_SQLITE)
            try:
                await self._sqlite.execute("ALTER TABLE profile ADD COLUMN mode TEXT DEFAULT ''")
                await self._sqlite.commit()
            except Exception:
                pass  # column already exists
            await self._sqlite.commit()
            logger.warning(
                "Memory: SQLite fallback (ephemeral on Render free). "
                "Set DATABASE_URL to a free Neon Postgres for permanent memory."
            )

    async def close(self):
        if self._pool:
            await self._pool.close()
        if self._sqlite:
            await self._sqlite.close()

    # ── low-level helpers ──────────────────────────────────────────────────────

    async def _exec_pg(self, sql: str, *args):
        async with self._pool.acquire() as con:
            return await con.execute(sql, *args)

    async def _exec(self, sql: str, args: tuple = ()):
        if self._pg:
            async with self._pool.acquire() as con:
                await con.execute(_pg(sql), *args)
        else:
            await self._sqlite.execute(sql, args)
            await self._sqlite.commit()

    async def _fetchone(self, sql: str, args: tuple = ()):
        if self._pg:
            async with self._pool.acquire() as con:
                row = await con.fetchrow(_pg(sql), *args)
                return tuple(row) if row else None
        async with self._sqlite.execute(sql, args) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, args: tuple = ()):
        if self._pg:
            async with self._pool.acquire() as con:
                rows = await con.fetch(_pg(sql), *args)
                return [tuple(r) for r in rows]
        async with self._sqlite.execute(sql, args) as cur:
            return await cur.fetchall()

    # ── cache ──────────────────────────────────────────────────────────────────

    async def ensure_loaded(self, uid: int):
        """Load this user's dossier + facts into the RAM cache (once)."""
        if uid in self._cache:
            return
        profile = await self._load_profile(uid)
        facts = await self._load_facts(uid)
        self._cache[uid] = {"profile": profile, "facts": facts}

    def cached_block(self, uid: int) -> str:
        """Synchronous context string for the system prompt (from RAM cache)."""
        data = self._cache.get(uid)
        if not data:
            return ""
        p = data["profile"]
        facts = data["facts"]
        parts: list[str] = []
        if p.get("name"):
            parts.append(f"Имя пользователя: {p['name']}.")
        if p.get("about"):
            parts.append(f"О пользователе: {p['about']}")
        if p.get("goals"):
            parts.append(f"Его цели: {p['goals']}")
        if p.get("preferences"):
            parts.append(f"Предпочтения: {p['preferences']}")
        if facts:
            joined = "; ".join(facts[-_MAX_FACTS:])
            parts.append(f"Что я о нём помню: {joined}")
        if not parts:
            return ""
        return "ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ (используй это, помни как близкий человек): " + " ".join(parts)

    def cached_mode(self, uid: int) -> str:
        """Synchronous personality-mode id from cache (for the system prompt)."""
        data = self._cache.get(uid)
        return (data["profile"].get("mode") or "") if data else ""

    async def get_mode(self, uid: int) -> str:
        await self.ensure_loaded(uid)
        return self._cache[uid]["profile"].get("mode", "")

    async def set_mode(self, uid: int, mode: str):
        await self.set_profile_field(uid, "mode", mode)

    # ── profile ────────────────────────────────────────────────────────────────

    async def _load_profile(self, uid: int) -> dict:
        row = await self._fetchone(
            "SELECT name, about, goals, preferences, mode FROM profile WHERE user_id=?", (uid,)
        )
        if not row:
            return {}
        return {"name": row[0] or "", "about": row[1] or "",
                "goals": row[2] or "", "preferences": row[3] or "",
                "mode": row[4] or ""}

    async def get_profile(self, uid: int) -> dict:
        await self.ensure_loaded(uid)
        return dict(self._cache[uid]["profile"])

    async def set_profile_field(self, uid: int, field: str, value: str):
        if field not in ("name", "about", "goals", "preferences", "mode"):
            return
        await self.ensure_loaded(uid)
        now = datetime.now().isoformat()
        cur = self._cache[uid]["profile"]
        vals = {f: (value if f == field else cur.get(f, "")) for f in _PROFILE_FIELDS}
        # Upsert all known fields; only `field` actually changes
        await self._exec(
            "INSERT INTO profile(user_id, name, about, goals, preferences, mode, updated_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            f"{field}=excluded.{field}, updated_at=excluded.updated_at",
            (uid, vals["name"], vals["about"], vals["goals"],
             vals["preferences"], vals["mode"], now),
        )
        cur[field] = value

    # ── facts ──────────────────────────────────────────────────────────────────

    async def _load_facts(self, uid: int) -> list:
        rows = await self._fetchall(
            "SELECT fact FROM facts WHERE user_id=? ORDER BY id ASC", (uid,)
        )
        return [r[0] for r in rows]

    async def add_fact(self, uid: int, fact: str) -> bool:
        fact = (fact or "").strip()
        if not fact:
            return False
        await self.ensure_loaded(uid)
        existing = self._cache[uid]["facts"]
        # Skip near-duplicates (case-insensitive substring either way)
        low = fact.lower()
        for f in existing:
            fl = f.lower()
            if low == fl or low in fl or fl in low:
                return False
        await self._exec(
            "INSERT INTO facts(user_id, fact, ts) VALUES(?,?,?)",
            (uid, fact, datetime.now().isoformat()),
        )
        existing.append(fact)
        return True

    async def get_facts(self, uid: int) -> list:
        await self.ensure_loaded(uid)
        return list(self._cache[uid]["facts"])

    async def clear(self, uid: int):
        await self._exec("DELETE FROM facts WHERE user_id=?", (uid,))
        await self._exec("DELETE FROM profile WHERE user_id=?", (uid,))
        self._cache.pop(uid, None)

    async def observe(self, uid: int, gemini, user_text: str, reply_text: str = ""):
        """Background learning: extract durable facts from an exchange and store
        them. Safe to fire-and-forget (asyncio.create_task)."""
        try:
            facts = await gemini.extract_facts(user_text, reply_text)
            for f in facts:
                added = await self.add_fact(uid, f)
                if added:
                    logger.info(f"Learned about {uid}: {f}")
        except Exception as e:
            logger.debug(f"observe: {e}")


def _pg(sql: str) -> str:
    """Convert '?' placeholders to Postgres '$1, $2, ...'."""
    out, n = [], 0
    for ch in sql:
        if ch == "?":
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
    return "".join(out)


_PROFILE_FIELDS = ("name", "about", "goals", "preferences", "mode")

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS profile (
    user_id     INTEGER PRIMARY KEY,
    name        TEXT DEFAULT '',
    about       TEXT DEFAULT '',
    goals       TEXT DEFAULT '',
    preferences TEXT DEFAULT '',
    mode        TEXT DEFAULT '',
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS facts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    fact    TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id);
"""

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS profile (
    user_id     BIGINT PRIMARY KEY,
    name        TEXT DEFAULT '',
    about       TEXT DEFAULT '',
    goals       TEXT DEFAULT '',
    preferences TEXT DEFAULT '',
    mode        TEXT DEFAULT '',
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS facts (
    id      BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    fact    TEXT NOT NULL,
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id);
"""
