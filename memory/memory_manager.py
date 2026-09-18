"""
ДЖАРВИС — Менеджер долгосрочной памяти

ХРАНЕНИЕ И БЮДЖЕТ ПРОМПТА — РАЗНЫЕ ЗАДАЧИ
    Раньше `format_memory_for_prompt` вываливала в системную инструкцию ВСЮ
    память целиком. Пока фактов десяток, это незаметно; на сотне каждый
    реконнект (а их за вечер бывает несколько) тащит простыню, за которую
    платят токенами и задержкой первого слова.

    Теперь в промпт едет ЯДРО: раздел «Личность» целиком плюс самые свежие
    факты из остальных категорий, пока не кончится бюджет. Остальное лежит на
    диске и достаётся по запросу — `search_memory()` за инструментом
    `recall_memory`.

    Тонкость, которую легко упустить: модель не может найти то, о чём не
    знает. Поэтому в промпт идёт ещё и ИНДЕКС КЛЮЧЕЙ, не влезших в бюджет, —
    список `[ТАКЖЕ ПОМНЮ]`. Без него «кто такая Азиза?» получает «не знаю»,
    пока `aziza_sister` лежит в файле непрочитанным. Индекс перемешивает
    категории по кругу, а не сортирует по свежести: отсортированный так же,
    как ядро, он на сорока предпочтениях вытолкнул бы ровно ту единственную
    запись, ради которой существует.
"""

import os
import sys
import time
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_MEMORY_FILE = _BASE / "memory" / "data.json"

# Гарантируем что core/ доступен (memory/ соседний с core/)
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core.storage import atomic_write_json, safe_read_json

# Сколько символов фактов кладём в системную инструкцию. Замерено: на 60
# фактах ядро укладывается примерно в тысячу символов — меньше, чем занимала
# половина прежней выгрузки.
PROMPT_BUDGET_CHARS = int(os.getenv("JARVIS_MEMORY_BUDGET", "1200"))

# Сколько ключей показываем в индексе. Список длиннее этого сам становится
# простынёй, ради борьбы с которой всё и затевалось.
INDEX_LIMIT = 40

# Предохранитель от разрастания, а не рабочий лимит: обычное использование до
# него не доходит. Ничего не удаляем молча — см. `over_limit()`.
MAX_ENTRIES = 5000

_CATEGORY_RU = {
    "identity":      "Личность",
    "preferences":   "Предпочтения",
    "projects":      "Проекты",
    "relationships": "Отношения",
    "wishes":        "Планы и желания",
    "notes":         "Заметки",
}

# Раздел, который едет в промпт целиком: без имени и города ассистент
# бесполезен, сколько бы предпочтений он ни помнил.
_CORE_CATEGORY = "identity"


def load_memory() -> dict:
    return safe_read_json(_MEMORY_FILE, default={})


def update_memory(patch: dict):
    """Дописывает факты. Каждой записи проставляется время — по нему потом
    решается, что попадёт в ядро промпта, и его же видит пользователь."""
    mem = load_memory()
    now = time.time()
    for category, items in patch.items():
        if not isinstance(items, dict):
            continue
        mem.setdefault(category, {})
        for key, val in items.items():
            entry = dict(val) if isinstance(val, dict) else {"value": val}
            entry.setdefault("value", "")
            entry["at"] = now
            mem[category][key] = entry
    atomic_write_json(_MEMORY_FILE, mem)
    return mem


def forget(key: str, category: str | None = None) -> bool:
    """Удаляет факт. Без категории ищет ключ во всех. True, если что-то удалил."""
    if not key:
        return False
    mem = load_memory()
    removed = False
    cats = [category] if category else list(mem.keys())
    for cat in cats:
        items = mem.get(cat)
        if isinstance(items, dict) and key in items:
            items.pop(key)
            removed = True
    if removed:
        atomic_write_json(_MEMORY_FILE, mem)
    return removed


def _value_of(val) -> str:
    if isinstance(val, dict):
        return str(val.get("value", ""))
    return str(val)


def _time_of(val) -> float:
    if isinstance(val, dict):
        try:
            return float(val.get("at", 0.0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def all_entries(memory: dict | None = None) -> list[dict]:
    """Плоский список фактов: категория, ключ, значение, время. Самое свежее
    первым. Используется поиском, индексом и панелью памяти в интерфейсе."""
    mem = load_memory() if memory is None else memory
    out = []
    for category, items in (mem or {}).items():
        if not isinstance(items, dict):
            continue
        for key, val in items.items():
            out.append({
                "category": category,
                "key":      key,
                "value":    _value_of(val),
                "at":       _time_of(val),
            })
    out.sort(key=lambda e: e["at"], reverse=True)
    return out


def filter_entries(entries: list[dict], query: str) -> list[dict]:
    """Отбор для панели памяти: подстрока в ключе, значении или категории.

    Здесь СОЗНАТЕЛЬНО не используется `_score`. Тот взвешивает слова, потому
    что отвечает модели на вопрос «что ты обо мне знаешь». Человек, который
    печатает в поле фильтра, ждёт другого: он видит список и сужает его,
    буква за буквой. Ранжирование в этот момент выглядит как пропажа строк.
    """
    искомое = (query or "").strip().lower()
    if not искомое:
        return list(entries)
    return [
        з for з in entries
        if искомое in з["key"].lower()
        or искомое in з["value"].lower()
        or искомое in з["category"].lower()
    ]


def over_limit(memory: dict | None = None) -> bool:
    """Предохранитель сработал. Ничего не удаляем — сообщаем наверх, чтобы это
    попало в лог, который читают, а не в stdout, который нет."""
    return len(all_entries(memory)) > MAX_ENTRIES


def _score(query_words: list[str], entry: dict) -> int:
    """Совпадение по ключу весит больше, чем по значению: спрашивают обычно
    именно тем словом, которым факт назван."""
    key = entry["key"].lower().replace("_", " ")
    value = entry["value"].lower()
    cat = entry["category"].lower()
    score = 0
    for word in query_words:
        if word in key:
            score += 3
        if word in value:
            score += 2
        if word in cat:
            score += 1
    return score


def search_memory(query: str, limit: int = 8) -> list[dict]:
    """Локальный поиск по всей памяти. Без сети и без второй модели.

    Пустой запрос возвращает самые свежие факты — так работает «что ты обо мне
    помнишь?»."""
    entries = all_entries()
    query = (query or "").strip().lower()
    if not query:
        return entries[:limit]

    words = [w for w in query.replace("_", " ").split() if w]
    scored = [(_score(words, e), e) for e in entries]
    hits = [e for s, e in sorted(scored, key=lambda p: (-p[0], -p[1]["at"])) if s > 0]
    return hits[:limit]


def _index_keys(rest: list[dict], limit: int = INDEX_LIMIT) -> list[str]:
    """Ключи, не влезшие в бюджет, — по кругу через категории.

    Именно поэтому не сортировкой: память с сорока предпочтениями и одним
    родственником при сортировке по свежести показала бы сорок предпочтений."""
    by_cat: dict[str, list[str]] = {}
    for e in rest:
        by_cat.setdefault(e["category"], []).append(e["key"])

    out: list[str] = []
    while len(out) < limit and any(by_cat.values()):
        for cat in list(by_cat.keys()):
            if not by_cat[cat]:
                continue
            out.append(by_cat[cat].pop(0))
            if len(out) >= limit:
                break
    return out


def format_memory_for_prompt(memory: dict) -> str:
    """Ядро памяти для системной инструкции плюс индекс остального."""
    if not memory:
        return ""

    entries = all_entries(memory)
    if not entries:
        return ""

    core = [e for e in entries if e["category"] == _CORE_CATEGORY]
    rest = [e for e in entries if e["category"] != _CORE_CATEGORY]

    chosen = list(core)
    budget = PROMPT_BUDGET_CHARS - sum(len(e["key"]) + len(e["value"]) + 4 for e in core)
    left_out: list[dict] = []
    for entry in rest:
        cost = len(entry["key"]) + len(entry["value"]) + 4
        if budget - cost >= 0:
            chosen.append(entry)
            budget -= cost
        else:
            left_out.append(entry)

    by_cat: dict[str, list[dict]] = {}
    for entry in chosen:
        by_cat.setdefault(entry["category"], []).append(entry)

    lines = ["[ДОЛГОСРОЧНАЯ ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ]"]
    for category, items in by_cat.items():
        lines.append(f"\n{_CATEGORY_RU.get(category, category)}:")
        for entry in items:
            lines.append(f"  {entry['key']}: {entry['value']}")

    if left_out:
        keys = _index_keys(left_out)
        lines.append(
            "\n[ТАКЖЕ ПОМНЮ] Эти факты сохранены, но в инструкцию не влезли. "
            "Если разговор коснётся любого из них — вызови recall_memory, "
            "прежде чем сказать, что не знаешь:"
        )
        lines.append("  " + ", ".join(keys))
        if len(left_out) > len(keys):
            lines.append(f"  …и ещё {len(left_out) - len(keys)} записей — их тоже ищет recall_memory.")

    lines.append("")
    return "\n".join(lines)


def format_search_results(results: list[dict]) -> str:
    """Человеческий ответ инструмента recall_memory."""
    if not results:
        return "В памяти ничего подходящего не нашлось."
    parts = []
    for entry in results:
        cat = _CATEGORY_RU.get(entry["category"], entry["category"])
        parts.append(f"{entry['key']} ({cat}): {entry['value']}")
    return "Из памяти: " + "; ".join(parts) + "."
