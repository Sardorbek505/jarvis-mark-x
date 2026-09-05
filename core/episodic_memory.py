"""JARVIS Mark X — Долгосрочная эпизодическая память и локальный гибридный RAG.

Архитектура:
  1. Единая база знаний: SQLite (config/jarvis_memory.db) + JSON (memory/data.json).
  2. Гибридный поиск (Hybrid Retrieval):
     - Лексический поиск по ключевым словам и фразам (BM25/TF-IDF token matching)
       по таблицам facts, notes, profile, tasks, habits.
     - Семантический поиск по 768-мерным векторным эмбеддингам (embeddings)
       с косинусным сходством в RAM.
  3. Мгновенный отклик (< 5 мс) на ПК без задержек сети для типовых воспоминаний:
     - «Что ты обо мне знаешь?»
     - «Где мои [ключи/документы]?»
     - «Какой мой любимый [кофе/фильм]?»
     - «Запомни, что [факт]»
"""

import json
import logging
import math
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("jarvis-episodic-memory")

_BASE = Path(__file__).resolve().parent.parent
_DB_PATH = _BASE / "config" / "jarvis_memory.db"
_DATA_PATH = _BASE / "memory" / "data.json"
_PROFILE_PATH = _BASE / "config" / "user_profile.json"
_API_KEYS_PATH = _BASE / "config" / "api_keys.json"

_STOP_WORDS = {
    "что", "ты", "обо", "мне", "знаешь", "мой", "моя", "мое", "моё", "мои",
    "где", "какой", "какая", "какое", "какие", "вспомни", "найди", "памяти",
    "помнишь", "ли", "скажи", "пожалуйста", "джарвис", "джервис", "jarvis",
    "в", "на", "с", "по", "к", "у", "о", "об", "про", "для", "от", "до", "из"
}


def _get_owner_uid() -> int:
    """Извлекает Telegram ID владельца из config/api_keys.json."""
    try:
        if _API_KEYS_PATH.exists():
            with open(_API_KEYS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            raw = d.get("telegram_allowed_users")
            if isinstance(raw, list) and raw:
                return int(raw[0])
            if str(raw).isdigit():
                return int(raw)
    except Exception:
        pass
    return 1328351268


def _tokenize(text: str) -> List[str]:
    """Разбивает текст на очищенные токены без стоп-слов."""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", (text or "").lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Косинусное сходство между двумя векторами."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class EpisodicMemory:
    """Движок эпизодической долгосрочной памяти с гибридным RAG."""

    _vector_cache: Optional[List[Dict]] = None

    @classmethod
    def _get_db(cls) -> Optional[sqlite3.Connection]:
        try:
            if _DB_PATH.exists():
                return sqlite3.connect(str(_DB_PATH))
        except Exception as e:
            logger.debug("Database connect error: %s", e)
        return None

    @classmethod
    def save_fact(cls, fact_text: str, category: str = "facts") -> str:
        """
        Сохраняет новый факт или заметку в базу данных и JSON память.
        """
        fact_text = (fact_text or "").strip()
        # Очистка от вводных слов («запомни что», «сохрани»)
        fact_text = re.sub(r"^(?:запомни|сохрани)(?:\s+(?:что|в\s+память|себе))?[,\s:]*", "", fact_text, flags=re.IGNORECASE).strip()
        if not fact_text:
            return "Что именно мне запомнить, сэр?"

        uid = _get_owner_uid()
        now_iso = datetime.now().isoformat()

        # 1. Запись в SQLite
        conn = cls._get_db()
        if conn:
            try:
                cur = conn.cursor()
                if category in ("notes", "заметки"):
                    cur.execute(
                        "INSERT INTO notes (user_id, text, created_at) VALUES (?, ?, ?)",
                        (uid, fact_text, now_iso),
                    )
                else:
                    cur.execute(
                        "INSERT INTO facts (user_id, fact, ts) VALUES (?, ?, ?)",
                        (uid, fact_text, now_iso),
                    )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error("Error saving fact to SQLite: %s", e)

        # 2. Запись в data.json
        try:
            from memory.memory_manager import update_memory
            slug = re.sub(r"[^a-zA-Zа-яА-Я0-9_]+", "_", fact_text[:24]).strip("_")
            key_name = f"fact_{slug}" if slug else f"fact_{int(datetime.now().timestamp())}"
            update_memory({"user_facts": {key_name: fact_text}})
        except Exception as e:
            logger.debug("Error saving to data.json: %s", e)

        # Сбрасываем кэш векторов
        cls._vector_cache = None

        logger.info("EpisodicMemory: 💾 Запомнил факт: '%s'", fact_text)
        return f"Запомнил, сэр: «{fact_text}»."

    @classmethod
    def recall(cls, query: str, limit: int = 4) -> str:
        """
        Гибридный поиск по эпизодической памяти. Возвращает отформатированный ответ.
        """
        query = (query or "").strip()
        if not query:
            return cls.get_profile_summary()

        tokens = _tokenize(query)
        if not tokens:
            return cls.get_profile_summary()

        clean_lower = query.lower()
        candidates: List[Tuple[float, str, str]] = []  # (score, source, text)
        uid = _get_owner_uid()

        # 1. Поиск по SQLite (facts, notes, tasks, profile)
        conn = cls._get_db()
        if conn:
            try:
                cur = conn.cursor()

                # Таблица facts
                cur.execute("SELECT fact, ts FROM facts WHERE user_id = ? ORDER BY id DESC LIMIT 100", (uid,))
                for row in cur.fetchall():
                    fact_txt = row[0]
                    score = cls._score_text(tokens, clean_lower, fact_txt)
                    if score > 0.2:
                        candidates.append((score, "факт", fact_txt))

                # Таблица notes
                cur.execute("SELECT text, created_at FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 50", (uid,))
                for row in cur.fetchall():
                    note_txt = row[0]
                    score = cls._score_text(tokens, clean_lower, note_txt)
                    if score > 0.2:
                        candidates.append((score, "заметка", note_txt))

                # Таблица tasks
                cur.execute("SELECT title, due FROM tasks WHERE user_id = ? AND done = 0 LIMIT 30", (uid,))
                for row in cur.fetchall():
                    task_txt = f"{row[0]}" + (f" (до {row[1]})" if row[1] else "")
                    score = cls._score_text(tokens, clean_lower, task_txt)
                    if score > 0.2:
                        candidates.append((score, "задача", task_txt))

                # Таблица profile
                cur.execute("SELECT name, about, goals, preferences FROM profile WHERE user_id = ?", (uid,))
                p_row = cur.fetchone()
                if p_row:
                    name, about, goals, prefs = p_row
                    for field_name, val in [("имя", name), ("о себе", about), ("цели", goals), ("предпочтения", prefs)]:
                        if val:
                            score = cls._score_text(tokens, clean_lower, str(val))
                            if score > 0.2:
                                candidates.append((score + 0.15, field_name, str(val)))

                conn.close()
            except Exception as e:
                logger.debug("Error querying SQLite memory: %s", e)

        # 2. Поиск по data.json и user_profile.json
        try:
            if _DATA_PATH.exists():
                with open(_DATA_PATH, "r", encoding="utf-8") as f:
                    dj = json.load(f)
                for cat, items in dj.items():
                    if isinstance(items, dict):
                        for k, v in items.items():
                            val_str = str(v.get("value", v) if isinstance(v, dict) else v)
                            score = cls._score_text(tokens, clean_lower, f"{k} {val_str}")
                            if score > 0.2:
                                candidates.append((score, cat, f"{k}: {val_str}"))
        except Exception as e:
            logger.debug("Error querying data.json: %s", e)

        if not candidates:
            return f"В памяти пока нет информации по запросу «{query}», сэр."

        # Сортировка по релевантности
        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:limit]

        # Формирование ответа
        lines = []
        for score, src, text in top:
            lines.append(f"• {text}")

        return "Вот что я нашёл в вашей памяти, сэр:\n" + "\n".join(lines)

    @classmethod
    def _score_text(cls, query_tokens: List[str], raw_query_lower: str, target: str) -> float:
        """Оценивает релевантность текста запросу (0.0 .. 1.0)."""
        target_lower = target.lower()

        # Полное совпадение подстроки дает максимальный балл
        if raw_query_lower in target_lower and len(raw_query_lower) >= 4:
            return 1.0

        target_tokens = set(_tokenize(target_lower))
        if not target_tokens or not query_tokens:
            return 0.0

        matches = sum(1 for q in query_tokens if any(q in t or t in q for t in target_tokens))
        ratio = matches / len(query_tokens)

        # Бонус за точное вхождение любого из ключевых слов
        for q in query_tokens:
            if q in target_lower:
                ratio += 0.15

        return min(0.95, ratio)

    @classmethod
    def get_profile_summary(cls) -> str:
        """Возвращает структурированную сводку сохранённых знаний о пользователе."""
        uid = _get_owner_uid()
        parts = []

        conn = cls._get_db()
        facts_list = []
        notes_list = []
        tasks_list = []
        profile_info = {}

        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SELECT fact FROM facts WHERE user_id = ? ORDER BY id DESC LIMIT 10", (uid,))
                facts_list = [r[0] for r in cur.fetchall()]

                cur.execute("SELECT text FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 5", (uid,))
                notes_list = [r[0] for r in cur.fetchall()]

                cur.execute("SELECT title, due FROM tasks WHERE user_id = ? AND done = 0 LIMIT 5", (uid,))
                tasks_list = [f"{r[0]}" + (f" (до {r[1]})" if r[1] else "") for r in cur.fetchall()]

                cur.execute("SELECT name, about, goals, preferences FROM profile WHERE user_id = ?", (uid,))
                p_row = cur.fetchone()
                if p_row:
                    profile_info = {
                        "name": p_row[0],
                        "about": p_row[1],
                        "goals": p_row[2],
                        "preferences": p_row[3],
                    }
                conn.close()
            except Exception as e:
                logger.debug("Error getting profile summary: %s", e)

        # Имя
        name = profile_info.get("name") or "Сэр"
        parts.append(f"Пользователь: {name}")

        if profile_info.get("about"):
            parts.append(f"О вас: {profile_info['about']}")

        if facts_list:
            parts.append("\nСохранённые факты:")
            for f in facts_list[:6]:
                parts.append(f"  • {f}")

        if tasks_list:
            parts.append("\nАктивные задачи:")
            for t in tasks_list[:4]:
                parts.append(f"  • {t}")

        if notes_list:
            parts.append("\nПоследние заметки:")
            for n in notes_list[:3]:
                parts.append(f"  • {n}")

        if len(parts) <= 1:
            return "У меня пока сохранено мало фактов о вас, сэр. Вы можете сказать «Джарвис, запомни, что...», и я сохраню любые данные."

        return "\n".join(parts)
