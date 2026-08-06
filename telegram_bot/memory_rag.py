"""Semantic memory (RAG) — phase 2 of "remember everything".

Every message JARVIS already stores durably; this layer makes it USABLE: each
user message/fact is embedded once, and on every turn we pull the most relevant
snippets from the ENTIRE history (not just the last 40) and inject them into the
prompt. Cosine similarity in pure Python over 768-dim vectors — fast enough for
a personal assistant, no extra deps.
"""
import asyncio
import logging
import math

logger = logging.getLogger("jarvis-rag")

_MIN_LEN = 3       # skip trivially short texts
_MIN_SIM = 0.55    # similarity floor for a hit to be worth injecting

# Векторы в РАМ, а не из базы на каждом ходу.
#
# Раньше retrieve() на КАЖДОЕ сообщение выгружал все эмбеддинги пользователя
# (SELECT по всей таблице), разбирал JSON каждого вектора по 768 чисел и только
# потом считал косинус. При 73 записях это уже сотни миллисекунд и полмегабайта
# трафика из Neon на ровном месте, а растёт линейно: к тысяче записей ход стал
# бы секундным. Данные при этом между ходами не меняются — только дописываются.
_VECS: dict[int, list] = {}


def forget_cached(uid: int) -> None:
    """Сбросить кэш векторов — после /forget, иначе стёртое всплывёт в поиске."""
    _VECS.pop(uid, None)


async def _vectors(memory, uid: int) -> list:
    cached = _VECS.get(uid)
    if cached is None:
        cached = await memory.all_embeddings(uid)
        _VECS[uid] = cached
    return cached


def _cosine(a: list, b: list) -> float:
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


async def index(memory, gemini, uid: int, kind: str, text: str) -> bool:
    """Embed + store one text (deduped). Safe to fire-and-forget. Returns True
    if a new embedding was stored."""
    text = (text or "").strip()
    if len(text) < _MIN_LEN:
        return False
    try:
        if await memory.has_embedding(uid, text):
            return False
        vec = await gemini.embed(text)
        if not vec:
            return False
        await memory.add_embedding(uid, kind, text, vec)
        cached = _VECS.get(uid)
        if cached is not None:          # держим кэш в согласии с базой
            cached.append({"kind": kind, "text": text, "vec": vec})
        return True
    except Exception as e:
        logger.debug(f"index: {e}")
        return False


async def retrieve(memory, gemini, uid: int, query: str, k: int = 6) -> list:
    """Top-k most relevant stored snippets for the query (semantic). [] on any
    failure — retrieval is a nice-to-have, never breaks the reply."""
    query = (query or "").strip()
    if len(query) < _MIN_LEN:
        return []
    # Искать воспоминания по «ок» и «спасибо» нечего, а вызов эмбеддинга это
    # стоит — он был четвёртым обращением к модели на каждое сообщение.
    from telegram_bot.memory_store import worth_learning
    if not worth_learning(query):
        return []
    try:
        qv = await gemini.embed(query)
        if not qv:
            return []
        rows = await _vectors(memory, uid)
        if not rows:
            return []
        scored = [(_cosine(qv, r["vec"]), r["text"]) for r in rows]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for s, t in scored[:k] if s >= _MIN_SIM]
    except Exception as e:
        logger.debug(f"retrieve: {e}")
        return []


def make_recall_provider(memory, gemini, recall_module):
    """Build the async recall provider used by gemini.chat: semantic retrieval
    over the whole history + the keyword note search, merged into one context
    string. Bound to a specific memory/gemini pair."""
    async def provider(uid: int, text: str) -> str:
        parts = []
        sem = await retrieve(memory, gemini, uid, text, k=6)
        if sem:
            # Индексируются ТОЛЬКО сообщения пользователя (см. _persist_exchange),
            # а подпись гласила «ты говорил это раньше». В системном промпте «ты» —
            # это сам ассистент, то есть чужие слова подавались ему как свои:
            # реплика «мне 21 год» выглядела утверждением JARVIS о себе.
            parts.append("ИЗ ПРОШЛЫХ СООБЩЕНИЙ ПОЛЬЗОВАТЕЛЯ (это его слова, не твои — "
                         "используй, если уместно): " + "; ".join(sem))
        try:
            kw = await recall_module.search(memory, uid, text)
        except Exception as exc:
            # Тихо выключенный поиск по памяти выглядит как «Джарвис забыл»,
            # причём ответ приходит уверенный — просто без фактов.
            logger.warning("Поиск по памяти не отработал для %s: %s", uid, exc)
            kw = ""
        if kw:
            parts.append(kw)
        return "\n".join(parts)
    return provider


async def backfill(memory, gemini, uid: int, limit: int = 400, delay: float = 0.15) -> int:
    """Index existing un-embedded messages/facts. Throttled. Returns count indexed."""
    texts = await memory.unembedded_texts(uid, limit=limit)
    done = 0
    for t in texts:
        if await index(memory, gemini, uid, "history", t):
            done += 1
            await asyncio.sleep(delay)  # gentle on the embedding endpoint
    if done:
        logger.info(f"RAG backfill for {uid}: indexed {done}")
    return done
