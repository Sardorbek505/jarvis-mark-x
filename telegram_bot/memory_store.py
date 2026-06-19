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
            await self._exec_pg("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS note TEXT DEFAULT ''")
            logger.info("Memory: Postgres connected (durable) ✅")
        else:
            import aiosqlite
            _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite = await aiosqlite.connect(_SQLITE_PATH)
            await self._sqlite.executescript(_SCHEMA_SQLITE)
            for _mig in (
                "ALTER TABLE profile ADD COLUMN mode TEXT DEFAULT ''",
                "ALTER TABLE contacts ADD COLUMN note TEXT DEFAULT ''",
            ):
                try:
                    await self._sqlite.execute(_mig)
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
        """Load this user's dossier + facts + open tasks into the RAM cache (once)."""
        if uid in self._cache:
            return
        profile = await self._load_profile(uid)
        facts = await self._load_facts(uid)
        tasks = await self._load_tasks(uid)
        contacts = await self.list_contacts(uid)
        schedule = await self.list_schedule(uid)
        projects = await self.list_projects(uid)
        self._cache[uid] = {
            "profile": profile, "facts": facts, "tasks": tasks,
            "contacts": contacts,
            "schedule": schedule,
            "projects": projects,
        }

    def cached_contacts(self, uid: int) -> list:
        """Whitelisted contact aliases from cache (for the system prompt)."""
        d = self._cache.get(uid)
        return d.get("contacts", []) if d else []

    def cached_schedule(self, uid: int) -> list:
        """Full weekly schedule from cache (for the system prompt)."""
        d = self._cache.get(uid)
        return d.get("schedule", []) if d else []

    async def _refresh_schedule_cache(self, uid: int):
        if uid in self._cache:
            self._cache[uid]["schedule"] = await self.list_schedule(uid)

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
        tasks = data.get("tasks", [])
        if tasks:
            tlines = []
            for t in tasks[:15]:
                due = t.get("due")
                when = ""
                if due:
                    try:
                        d = datetime.fromisoformat(due)
                        when = (" — " + d.strftime("%d.%m %H:%M")) if (d.hour or d.minute) \
                            else (" — " + d.strftime("%d.%m"))
                    except Exception:
                        pass
                tlines.append(f"{t['title']}{when}")
            parts.append("Его актуальные задачи/планы: " + "; ".join(tlines))
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

    # ── tasks / calendar ─────────────────────────────────────────────────────────

    async def _load_tasks(self, uid: int) -> list:
        rows = await self._fetchall(
            "SELECT id, title, due, done FROM tasks WHERE user_id=? AND done=0", (uid,)
        )
        items = [{"id": r[0], "title": r[1], "due": r[2]} for r in rows]
        return _sort_tasks(items)

    async def add_task(self, uid: int, title: str, due: Optional[str] = None) -> dict:
        title = (title or "").strip()
        await self.ensure_loaded(uid)
        await self._exec(
            "INSERT INTO tasks(user_id, title, due, done, created_at) VALUES(?,?,?,0,?)",
            (uid, title, due, datetime.now().isoformat()),
        )
        row = await self._fetchone(
            "SELECT id FROM tasks WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
        )
        task = {"id": row[0] if row else 0, "title": title, "due": due}
        self._cache[uid]["tasks"] = _sort_tasks(self._cache[uid]["tasks"] + [task])
        return task

    async def get_tasks(self, uid: int) -> list:
        await self.ensure_loaded(uid)
        return list(self._cache[uid]["tasks"])

    async def complete_task(self, uid: int, task_id: int) -> Optional[dict]:
        await self.ensure_loaded(uid)
        match = next((t for t in self._cache[uid]["tasks"] if t["id"] == task_id), None)
        if not match:
            return None
        await self._exec("UPDATE tasks SET done=1 WHERE id=? AND user_id=?", (task_id, uid))
        self._cache[uid]["tasks"] = [t for t in self._cache[uid]["tasks"] if t["id"] != task_id]
        return match

    # ── habits ───────────────────────────────────────────────────────────────────

    async def add_habit(self, uid: int, title: str) -> dict:
        title = (title or "").strip()
        await self._exec(
            "INSERT INTO habits(user_id, title, created_at) VALUES(?,?,?)",
            (uid, title, datetime.now().isoformat()),
        )
        row = await self._fetchone(
            "SELECT id FROM habits WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
        )
        return {"id": row[0] if row else 0, "title": title}

    async def _habit_days(self, habit_id: int) -> set:
        rows = await self._fetchall(
            "SELECT day FROM habit_checks WHERE habit_id=?", (habit_id,)
        )
        return {r[0] for r in rows}

    async def get_habits(self, uid: int, today: str) -> list:
        rows = await self._fetchall(
            "SELECT id, title FROM habits WHERE user_id=? ORDER BY id ASC", (uid,)
        )
        out = []
        for hid, title in rows:
            days = await self._habit_days(hid)
            out.append({
                "id": hid, "title": title,
                "done_today": today in days,
                "streak": _streak(days, today),
            })
        return out

    async def toggle_habit(self, uid: int, habit_id: int, day: str) -> Optional[bool]:
        owner = await self._fetchone(
            "SELECT id FROM habits WHERE id=? AND user_id=?", (habit_id, uid)
        )
        if not owner:
            return None
        exists = await self._fetchone(
            "SELECT 1 FROM habit_checks WHERE habit_id=? AND day=?", (habit_id, day)
        )
        if exists:
            await self._exec(
                "DELETE FROM habit_checks WHERE habit_id=? AND day=?", (habit_id, day)
            )
            return False
        await self._exec(
            "INSERT INTO habit_checks(habit_id, day) VALUES(?,?)", (habit_id, day)
        )
        return True

    async def delete_habit(self, uid: int, habit_id: int) -> bool:
        owner = await self._fetchone(
            "SELECT id FROM habits WHERE id=? AND user_id=?", (habit_id, uid)
        )
        if not owner:
            return False
        await self._exec("DELETE FROM habit_checks WHERE habit_id=?", (habit_id,))
        await self._exec("DELETE FROM habits WHERE id=? AND user_id=?", (habit_id, uid))
        return True

    # ── reminders (durable, timezone-correct: due stored as UTC ISO) ─────────────

    async def add_reminder(self, uid: int, text: str, due_utc_iso: str) -> dict:
        await self._exec(
            "INSERT INTO reminders(user_id, text, due, sent, created_at) VALUES(?,?,?,0,?)",
            (uid, (text or "").strip(), due_utc_iso, datetime.now().isoformat()),
        )
        row = await self._fetchone(
            "SELECT id FROM reminders WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
        )
        return {"id": row[0] if row else 0, "text": text, "due": due_utc_iso}

    async def get_due_reminders(self, now_utc_iso: str) -> list:
        rows = await self._fetchall(
            "SELECT id, user_id, text, due FROM reminders WHERE sent=0", ()
        )
        return [
            {"id": r[0], "user_id": r[1], "text": r[2], "due": r[3]}
            for r in rows if r[3] and r[3] <= now_utc_iso
        ]

    async def mark_reminder_sent(self, reminder_id: int):
        await self._exec("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))

    async def list_reminders(self, uid: int) -> list:
        rows = await self._fetchall(
            "SELECT id, text, due FROM reminders WHERE user_id=? AND sent=0 ORDER BY due ASC",
            (uid,),
        )
        return [{"id": r[0], "text": r[1], "due": r[2]} for r in rows]

    # ── meta (proactive bookkeeping) + recipients ────────────────────────────────

    async def set_meta(self, uid: int, key: str, value: str):
        await self._exec(
            "INSERT INTO meta(user_id, key, value) VALUES(?,?,?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (uid, key, value),
        )

    async def get_meta(self, uid: int, key: str) -> Optional[str]:
        row = await self._fetchone(
            "SELECT value FROM meta WHERE user_id=? AND key=?", (uid, key)
        )
        return row[0] if row else None

    # ── contacts whitelist (JARVIS Outbound) ─────────────────────────────────────

    async def add_contact(self, uid: int, alias: str, target: str, note: str = ""):
        await self._exec(
            "INSERT INTO contacts(user_id, alias, target, note, created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id, alias) DO UPDATE SET target=excluded.target, note=excluded.note",
            (uid, alias.strip().lower(), target.strip(), (note or "").strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        await self._refresh_contacts_cache(uid)

    async def _refresh_contacts_cache(self, uid: int):
        if uid in self._cache:
            self._cache[uid]["contacts"] = await self.list_contacts(uid)

    async def list_contacts(self, uid: int) -> list:
        rows = await self._fetchall(
            "SELECT alias, target, note FROM contacts WHERE user_id=? ORDER BY alias", (uid,)
        )
        return [{"alias": r[0], "target": r[1], "note": r[2] or ""} for r in rows]

    async def del_contact(self, uid: int, alias: str) -> bool:
        a = alias.strip().lower()
        existed = await self._fetchone(
            "SELECT 1 FROM contacts WHERE user_id=? AND alias=?", (uid, a)
        )
        if not existed:
            return False
        await self._exec("DELETE FROM contacts WHERE user_id=? AND alias=?", (uid, a))
        await self._refresh_contacts_cache(uid)
        return True

    async def resolve_contact(self, uid: int, name: str) -> Optional[str]:
        """Map a spoken/typed name to a whitelisted target (@username/phone/id).
        Exact alias match first, then a forgiving partial match."""
        n = (name or "").strip().lower()
        if not n:
            return None
        rows = await self._fetchall(
            "SELECT alias, target FROM contacts WHERE user_id=?", (uid,)
        )
        for alias, target in rows:
            if alias == n:
                return target
        for alias, target in rows:
            if n in alias or alias in n:
                return target
        return None

    # ── outbox (очередь Outbound: отправить, когда ПК онлайн) ────────────────────

    async def queue_outbound(self, uid: int, target: str, alias: str, message: str, as_voice: bool):
        await self._exec(
            "INSERT INTO outbox(user_id, target, alias, message, as_voice, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (uid, target, alias, message, 1 if as_voice else 0,
             datetime.now().isoformat(timespec="seconds")),
        )

    async def pending_outbound(self, uid: Optional[int] = None) -> list:
        if uid is None:
            rows = await self._fetchall(
                "SELECT id, user_id, target, alias, message, as_voice FROM outbox ORDER BY id", ()
            )
        else:
            rows = await self._fetchall(
                "SELECT id, user_id, target, alias, message, as_voice FROM outbox WHERE user_id=? ORDER BY id",
                (uid,),
            )
        return [{"id": r[0], "user_id": r[1], "target": r[2], "alias": r[3],
                 "message": r[4], "as_voice": bool(r[5])} for r in rows]

    async def delete_outbound(self, item_id: int):
        await self._exec("DELETE FROM outbox WHERE id=?", (item_id,))

    # ── notes (единая входящая: свободные мысли/идеи) ────────────────────────────

    async def add_note(self, uid: int, text: str) -> dict:
        text = text.strip()
        await self._exec(
            "INSERT INTO notes(user_id, text, created_at) VALUES(?,?,?)",
            (uid, text, datetime.now().isoformat(timespec="seconds")),
        )
        row = await self._fetchone(
            "SELECT id FROM notes WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
        )
        return {"id": row[0] if row else None, "text": text}

    async def list_notes(self, uid: int, limit: int = 50) -> list:
        rows = await self._fetchall(
            "SELECT id, text, created_at FROM notes WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit),
        )
        return [{"id": r[0], "text": r[1], "created_at": r[2]} for r in rows]

    async def delete_note(self, uid: int, note_id: int) -> bool:
        owner = await self._fetchone(
            "SELECT 1 FROM notes WHERE id=? AND user_id=?", (note_id, uid)
        )
        if not owner:
            return False
        await self._exec("DELETE FROM notes WHERE id=? AND user_id=?", (note_id, uid))
        return True

    # ── schedule (расписание пар) ────────────────────────────────────────────────

    async def add_class(self, uid: int, weekday: int, time: str, subject: str, location: str = "") -> dict:
        await self._exec(
            "INSERT INTO schedule(user_id, weekday, time, subject, location, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (uid, int(weekday), (time or "").strip(), subject.strip(), (location or "").strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        row = await self._fetchone(
            "SELECT id FROM schedule WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
        )
        await self._refresh_schedule_cache(uid)
        return {"id": row[0] if row else None}

    async def list_schedule(self, uid: int) -> list:
        rows = await self._fetchall(
            "SELECT id, weekday, time, subject, location FROM schedule WHERE user_id=? "
            "ORDER BY weekday, time", (uid,)
        )
        return [{"id": r[0], "weekday": r[1], "time": r[2], "subject": r[3], "location": r[4]} for r in rows]

    async def schedule_for_day(self, uid: int, weekday: int) -> list:
        rows = await self._fetchall(
            "SELECT id, weekday, time, subject, location FROM schedule WHERE user_id=? AND weekday=? "
            "ORDER BY time", (uid, int(weekday))
        )
        return [{"id": r[0], "weekday": r[1], "time": r[2], "subject": r[3], "location": r[4]} for r in rows]

    async def delete_class(self, uid: int, class_id: int) -> bool:
        owner = await self._fetchone(
            "SELECT 1 FROM schedule WHERE id=? AND user_id=?", (class_id, uid)
        )
        if not owner:
            return False
        await self._exec("DELETE FROM schedule WHERE id=? AND user_id=?", (class_id, uid))
        await self._refresh_schedule_cache(uid)
        return True

    async def clear_schedule(self, uid: int):
        await self._exec("DELETE FROM schedule WHERE user_id=?", (uid,))
        await self._refresh_schedule_cache(uid)

    # ── projects (трекер статусов проектов) ──────────────────────────────────────

    async def upsert_project(self, uid: int, name: str, status: str) -> dict:
        name = name.strip()
        await self._exec(
            "INSERT INTO projects(user_id, name, status, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
            (uid, name, status.strip(), datetime.now().isoformat(timespec="seconds")),
        )
        await self._refresh_projects_cache(uid)
        return {"name": name, "status": status.strip()}

    async def list_projects(self, uid: int) -> list:
        rows = await self._fetchall(
            "SELECT name, status, updated_at FROM projects WHERE user_id=? ORDER BY updated_at DESC",
            (uid,),
        )
        return [{"name": r[0], "status": r[1], "updated_at": r[2]} for r in rows]

    async def delete_project(self, uid: int, name: str) -> bool:
        n = name.strip()
        owner = await self._fetchone(
            "SELECT 1 FROM projects WHERE user_id=? AND lower(name)=lower(?)", (uid, n)
        )
        if not owner:
            return False
        await self._exec("DELETE FROM projects WHERE user_id=? AND lower(name)=lower(?)", (uid, n))
        await self._refresh_projects_cache(uid)
        return True

    def cached_projects(self, uid: int) -> list:
        d = self._cache.get(uid)
        return d.get("projects", []) if d else []

    async def _refresh_projects_cache(self, uid: int):
        if uid in self._cache:
            self._cache[uid]["projects"] = await self.list_projects(uid)

    async def all_user_ids(self) -> list:
        """Every user the bot knows — to deliver proactive briefings to."""
        rows = await self._fetchall(
            "SELECT user_id FROM profile "
            "UNION SELECT user_id FROM tasks "
            "UNION SELECT user_id FROM facts "
            "UNION SELECT user_id FROM meta"
        )
        return [r[0] for r in rows]

    async def clear(self, uid: int):
        rows = await self._fetchall("SELECT id FROM habits WHERE user_id=?", (uid,))
        for (hid,) in rows:
            await self._exec("DELETE FROM habit_checks WHERE habit_id=?", (hid,))
        await self._exec("DELETE FROM facts WHERE user_id=?", (uid,))
        await self._exec("DELETE FROM profile WHERE user_id=?", (uid,))
        await self._exec("DELETE FROM tasks WHERE user_id=?", (uid,))
        await self._exec("DELETE FROM habits WHERE user_id=?", (uid,))
        await self._exec("DELETE FROM reminders WHERE user_id=?", (uid,))
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


def _streak(days: set, today: str) -> int:
    """Count consecutive checked days ending today (or yesterday if today not yet
    checked, so the streak survives until a full day is actually missed)."""
    from datetime import date, timedelta
    try:
        cur = date.fromisoformat(today)
    except Exception:
        return 0
    if today not in days:
        cur = cur - timedelta(days=1)
    n = 0
    while cur.isoformat() in days:
        n += 1
        cur -= timedelta(days=1)
    return n


def _sort_tasks(items: list) -> list:
    """Dated tasks first (earliest due), then undated todos. Stable by id."""
    def key(t):
        due = t.get("due")
        return (0, due) if due else (1, "")
    return sorted(items, key=key)


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
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    title      TEXT NOT NULL,
    due        TEXT,
    done       INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    user_id INTEGER NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS habits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    title      TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS habit_checks (
    habit_id INTEGER NOT NULL,
    day      TEXT NOT NULL,
    PRIMARY KEY (habit_id, day)
);
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    due        TEXT NOT NULL,
    sent       INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
    user_id    INTEGER NOT NULL,
    alias      TEXT NOT NULL,
    target     TEXT NOT NULL,
    note       TEXT DEFAULT '',
    created_at TEXT,
    PRIMARY KEY (user_id, alias)
);
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    target     TEXT NOT NULL,
    alias      TEXT NOT NULL,
    message    TEXT NOT NULL,
    as_voice   INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS schedule (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    weekday    INTEGER NOT NULL,
    time       TEXT,
    subject    TEXT NOT NULL,
    location   TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    status     TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_habits_user ON habits(user_id);
CREATE INDEX IF NOT EXISTS idx_reminders_sent ON reminders(sent);
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
CREATE TABLE IF NOT EXISTS tasks (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    title      TEXT NOT NULL,
    due        TEXT,
    done       INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    user_id BIGINT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS habits (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    title      TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS habit_checks (
    habit_id BIGINT NOT NULL,
    day      TEXT NOT NULL,
    PRIMARY KEY (habit_id, day)
);
CREATE TABLE IF NOT EXISTS reminders (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    text       TEXT NOT NULL,
    due        TEXT NOT NULL,
    sent       INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
    user_id    BIGINT NOT NULL,
    alias      TEXT NOT NULL,
    target     TEXT NOT NULL,
    note       TEXT DEFAULT '',
    created_at TEXT,
    PRIMARY KEY (user_id, alias)
);
CREATE TABLE IF NOT EXISTS outbox (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    target     TEXT NOT NULL,
    alias      TEXT NOT NULL,
    message    TEXT NOT NULL,
    as_voice   INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS schedule (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    weekday    INTEGER NOT NULL,
    time       TEXT,
    subject    TEXT NOT NULL,
    location   TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    user_id    BIGINT NOT NULL,
    name       TEXT NOT NULL,
    status     TEXT,
    updated_at TEXT,
    PRIMARY KEY (user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_habits_user ON habits(user_id);
CREATE INDEX IF NOT EXISTS idx_reminders_sent ON reminders(sent);
"""
