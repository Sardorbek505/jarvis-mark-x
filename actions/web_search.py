"""Действие: найти в интернете и вернуть найденное текстом — чтобы ответить
вслух, а не открывать вкладку.

Раньше здесь был DuckDuckGo Instant Answer API: на русские запросы (новости,
цены, «кто выиграл») он почти всегда пуст, и тогда молча открывался браузер,
а у модели не было ни одного факта для ответа. Теперь — обычная выдача
(core/web_find.py): заголовки, выдержки и ссылки первых результатов.
"""
from core import web_find


def web_search(parameters: dict, player=None) -> str:
    query = (parameters.get("query") or "").strip()
    if not query:
        return "Укажите поисковый запрос."
    hits = web_find.search(query, limit=6)
    if not hits:
        return (f"Поиск по «{query}» ничего не дал (или нет связи с интернетом). "
                "Скажи честно, что не нашёл, и предложи открыть поиск в браузере.")
    if player:
        player.write_log(f"SYS: Поиск — {query}")
    parts = []
    for h in hits[:5]:
        snippet = h.snippet if len(h.snippet) <= 180 else h.snippet[:180].rsplit(" ", 1)[0] + "…"
        parts.append(f"{h.title} — {snippet}" if snippet else h.title)
    # Формат «По запросу «…»: a | b | c» читает и карточка результата.
    return f"По запросу «{query}»: " + " | ".join(parts)
