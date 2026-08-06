"""Semantic memory (RAG): cosine ranking + index/retrieve round-trip.

Uses a fake embedder (fixed text→vector map) so retrieval is deterministic and
offline — no Gemini calls.
"""
import pytest

from telegram_bot import memory_rag

UID = 555_000_006


class _FakeGemini:
    def __init__(self, vectors):
        self._v = vectors

    async def embed(self, text):
        return self._v.get((text or "").strip())


def test_cosine_identity_and_orthogonal():
    assert memory_rag._cosine([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
    assert memory_rag._cosine([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
    assert memory_rag._cosine([], [1]) == 0.0


@pytest.mark.asyncio
async def test_index_dedup(mem):
    g = _FakeGemini({"люблю плов": [1, 0, 0]})
    assert await memory_rag.index(mem, g, UID, "fact", "люблю плов") is True
    assert await memory_rag.index(mem, g, UID, "fact", "люблю плов") is False  # dup
    assert await mem.embedding_count(UID) == 1


@pytest.mark.asyncio
async def test_index_skips_short(mem):
    g = _FakeGemini({"ок": [1, 0, 0]})
    assert await memory_rag.index(mem, g, UID, "msg", "ок") is False
    assert await mem.embedding_count(UID) == 0


@pytest.mark.asyncio
async def test_retrieve_ranks_by_similarity(mem):
    vectors = {
        "люблю плов и шашлык": [1.0, 0.0, 0.0],
        "погода сегодня солнечная": [0.0, 1.0, 0.0],
        "мой брат программист": [0.0, 0.0, 1.0],
        "что я люблю поесть?": [0.95, 0.1, 0.0],  # query — closest to plov
    }
    g = _FakeGemini(vectors)
    for t in ("люблю плов и шашлык", "погода сегодня солнечная", "мой брат программист"):
        await memory_rag.index(mem, g, UID, "msg", t)

    hits = await memory_rag.retrieve(mem, g, UID, "что я люблю поесть?", k=2)
    assert hits and hits[0] == "люблю плов и шашлык"
    assert "погода сегодня солнечная" not in hits  # below similarity floor


@pytest.mark.asyncio
async def test_retrieve_empty_when_nothing_indexed(mem):
    g = _FakeGemini({"вопрос": [1, 0, 0]})
    assert await memory_rag.retrieve(mem, g, UID, "вопрос") == []


@pytest.mark.asyncio
async def test_clear_wipes_embeddings(mem):
    g = _FakeGemini({"факт о пользователе": [1, 0, 0]})
    await memory_rag.index(mem, g, UID, "fact", "факт о пользователе")
    await mem.clear(UID)
    assert await mem.embedding_count(UID) == 0


# ── авторство фрагментов ─────────────────────────────────────────────────────
class _Recall:
    async def search(self, memory, uid, text):
        return ""


class _AllSame:
    """Всё одинаково релевантно — в выдачу попадут все виды сразу."""
    def __init__(self, rows):
        self.rows = rows

    async def all_embeddings(self, uid):
        return self.rows


class _OneVec:
    async def embed(self, text):
        return [1.0, 0.0]


ROWS = [
    {"kind": "message",  "text": "мне 21 год",                    "vec": [1.0, 0.0]},
    {"kind": "link",     "text": "[Ссылка habr] статья про RAG",  "vec": [1.0, 0.0]},
    {"kind": "journal",  "text": "[2026-08-05] день был тяжёлый", "vec": [1.0, 0.0]},
    {"kind": "fact",     "text": "Живёт в Шымкенте",              "vec": [1.0, 0.0]},
]


@pytest.mark.asyncio
async def test_каждый_вид_подписан_своим_автором():
    """Рядом лежат слова пользователя и тексты, написанные моделью.

    Одна общая подпись врала бы: конспекты ссылок и записи дневника
    составляет ассистент, а не пользователь. Сначала подпись вообще
    гласила «ты говорил это раньше» — то есть чужие слова приходили
    модели как её собственные.
    """
    memory_rag.forget_cached(1)
    prov = memory_rag.make_recall_provider(_AllSame(ROWS), _OneVec(), _Recall())
    out = await prov(1, "расскажи, что знаешь обо мне")

    assert "ПОЛЬЗОВАТЕЛЬ ПИСАЛ РАНЬШЕ" in out
    assert "мне 21 год" in out
    # то, что писала модель, помечено как её работа
    assert "составлял ты, не он" in out
    assert "вёл ты о его днях" in out
    # и нигде слова пользователя не приписаны ассистенту
    assert "ты говорил это раньше" not in out


@pytest.mark.asyncio
async def test_незнакомый_вид_не_приписывается_никому():
    memory_rag.forget_cached(2)
    rows = [{"kind": "нечто", "text": "непонятное", "vec": [1.0, 0.0]}]
    prov = memory_rag.make_recall_provider(_AllSame(rows), _OneVec(), _Recall())
    out = await prov(2, "что там было")
    assert "ИЗ СОХРАНЁННОГО РАНЕЕ" in out
