"""
Действие: веб-поиск.

ЧТО БЫЛО НЕ ТАК
    Единственным источником был DuckDuckGo Instant Answer API
    (`api.duckduckgo.com/?q=...&format=json`). Это не поиск, а справочник
    определений: он знает, что такое «фотосинтез», и молчит про «цену RTX 5080»
    и «что случилось вчера». На молчание срабатывал запасной путь — открыть
    браузер, — и ассистент отвечал «открыл поиск в браузере» на вопрос,
    заданный голосом, когда человек до браузера не дотягивается.

    Поиском ассистент пользуется чаще любого другого инструмента: именно он
    стоит между «не знаю» и ответом.

КАК УСТРОЕНО СЕЙЧАС
    1. Gemini с включённым Google Search — модель ищет сама и отвечает по
       найденному, а не по памяти. Ответ сразу пригоден для произнесения вслух.
    2. Если ключа нет или запрос сорвался — разбор выдачи DuckDuckGo: заголовки
       и выдержки, из которых можно прочитать хотя бы суть.
    3. Instant Answer как последний источник фактов.
    4. Браузер — только когда не осталось ничего, и в ответе прямо сказано, что
       это не ответ на вопрос, а открытая вкладка.

    Режимы (`mode`) меняют не источник, а форму ответа: новости просят три
    свежих заголовка с датами, цена — число с валютой, сравнение — по пунктам.
    Ассистента слушают, а не читают, поэтому длина ответа ограничена везде.
"""

import json
import logging
import re
import urllib.parse
import urllib.request

_logger = logging.getLogger(__name__)

# Отдельный от Live API вызов: там своя модель и свой сокет.
SEARCH_MODEL = "gemini-2.5-flash"

_TIMEOUT_SEC = 12

# Сколько символов ответа считаем пригодным для произнесения. Длиннее —
# человек перестаёт слушать раньше, чем Джарвис договорит.
_MAX_SPOKEN = 700

_MODE_INSTRUCTIONS = {
    "search": (
        "Ответь на вопрос по найденному в поиске. Два-три предложения, "
        "самое важное первым."
    ),
    "news": (
        "Дай три САМЫЕ СВЕЖИЕ новости по теме. Для каждой: когда и что "
        "произошло, одним предложением. Если дата неизвестна — так и скажи."
    ),
    "research": (
        "Дай развёрнутый разбор темы: суть, ключевые факты, разные точки "
        "зрения. До семи предложений, без воды и без списков — это читают вслух."
    ),
    "price": (
        "Найди действующую цену. Назови число с валютой, где именно столько стоит, "
        "и на какое число эта цена. Если цены разнятся — назови диапазон."
    ),
    "compare": (
        "Сравни предметы по существу: чем отличаются, что кому подходит, "
        "и чем закончить выбор. Не перечисляй характеристики подряд — "
        "назови те, что действительно решают."
    ),
}

_MODE_ALIASES = {
    "новости": "news", "новость": "news",
    "исследование": "research", "разбор": "research",
    "цена": "price", "стоимость": "price",
    "сравнение": "compare", "сравни": "compare",
    "поиск": "search",
}


def _normalise_mode(raw: str) -> str:
    mode = (raw or "search").strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    return mode if mode in _MODE_INSTRUCTIONS else "search"


def _build_query(query: str, mode: str, items: list, aspect: str) -> str:
    """Собирает вопрос модели: запрос, форма ответа и уточнения режима."""
    parts = [_MODE_INSTRUCTIONS[mode]]

    if mode == "compare" and items:
        предметы = ", ".join(str(i) for i in items if i)
        parts.append(f"Сравнить нужно: {предметы}.")
    if aspect:
        parts.append(f"Интересует прежде всего: {aspect}.")

    parts.append(
        "Отвечай по-русски, обращайся «сэр», без markdown и без списков — "
        "ответ произносят вслух. Не выдумывай: если в поиске этого нет, "
        "скажи прямо, что не нашёл."
    )
    parts.append(f"Запрос: {query}")
    return "\n".join(parts)


def _gemini_search(query: str, mode: str, items: list, aspect: str) -> str:
    """Поиск глазами Gemini: модель ищет в Google и отвечает по найденному.

    Пустая строка означает «не сработало» — вызывающий переходит к DuckDuckGo.
    Ошибку ключа наверх не бросаем: у голосового поиска есть чем ответить и
    без него, а разбираться с ключом — работа запуска, не поиска."""
    try:
        from core.onboarding import ensure_gemini_key
        key = ensure_gemini_key(interactive=False)
    except Exception as exc:
        _logger.debug("Ключ для поиска недоступен: %s", exc)
        return ""
    if not key:
        return ""

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=SEARCH_MODEL,
            contents=_build_query(query, mode, items, aspect),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.3,
                max_output_tokens=800,
            ),
        )
        return (response.text or "").strip()
    except Exception as exc:
        _logger.warning("Поиск через Gemini не удался: %s", exc)
        return ""


_LINK_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*>(.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str) -> str:
    import html as _html
    return _html.unescape(_TAG_RE.sub("", raw)).strip()


def _ddg_results(query: str, limit: int = 3) -> list[tuple[str, str]]:
    """Заголовки и выдержки из выдачи DuckDuckGo.

    Разбор регуляркой, а не bs4: ради одного запасного пути тянуть в проект
    ещё одну зависимость незачем. Сломается разметка — вернём пустой список,
    и сработает следующий источник."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Language": "ru,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        _logger.warning("DuckDuckGo не ответил: %s", exc)
        return []

    out = []
    for title, snippet in _LINK_RE.findall(page)[:limit]:
        заголовок = _strip_html(title)
        выдержка = _strip_html(snippet)
        if заголовок:
            out.append((заголовок, выдержка))
    return out


def _instant_answer(query: str) -> str:
    """Справочник определений. Знает «что такое X» и почти ничего больше."""
    url = ("https://api.duckduckgo.com/?q=" + urllib.parse.quote(query)
           + "&format=json&no_redirect=1&no_html=1")
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        _logger.warning("Instant Answer не ответил: %s", exc)
        return ""

    for field in ("AbstractText", "Answer", "Definition"):
        value = str(data.get(field, "")).strip()
        if value:
            return value

    snippets = [
        t["Text"] for t in data.get("RelatedTopics", [])[:3]
        if isinstance(t, dict) and t.get("Text")
    ]
    return " ".join(snippets)


def _open_in_browser(query: str) -> str:
    """Последнее средство. Ответ честно говорит, что вопрос остался без ответа."""
    import subprocess
    import sys

    url = "https://duckduckgo.com/?q=" + urllib.parse.quote(query)
    try:
        if sys.platform == "win32":
            import os
            os.startfile(url)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception as exc:
        _logger.warning("Браузер не открылся: %s", exc)
        return (f"Сэр, найти ответ на «{query}» не удалось, и браузер тоже не "
                f"открылся. Похоже, сети нет.")
    return (f"Сэр, сам ответа не нашёл — открыл поиск по «{query}» в браузере. "
            f"Если вы не у экрана, переспросите, я попробую иначе.")


def _shorten(text: str) -> str:
    """Режет по границе предложения: оборванная на полуслове фраза вслух
    звучит как сбой, а не как краткость."""
    text = " ".join(text.split())
    if len(text) <= _MAX_SPOKEN:
        return text
    обрез = text[:_MAX_SPOKEN]
    точка = max(обрез.rfind(". "), обрез.rfind("! "), обрез.rfind("? "))
    return обрез[:точка + 1] if точка > _MAX_SPOKEN // 2 else обрез.rstrip() + "…"


def web_search(parameters: dict, player=None) -> str:
    query = str(parameters.get("query", "")).strip()
    if not query:
        return "Укажите поисковый запрос, сэр."

    mode = _normalise_mode(parameters.get("mode", ""))
    items = parameters.get("items") or []
    aspect = str(parameters.get("aspect", "")).strip()
    if isinstance(items, str):
        items = [items]

    if player:
        подпись = query if mode == "search" else f"{query} [{mode}]"
        player.write_log(f"SYS: Поиск — {подпись}")

    ответ = _gemini_search(query, mode, items, aspect)
    if ответ:
        return _shorten(ответ)

    результаты = _ddg_results(query, limit=3 if mode == "news" else 2)
    if результаты:
        куски = [f"{заголовок}. {выдержка}" if выдержка else заголовок
                 for заголовок, выдержка in результаты]
        return _shorten(f"По запросу «{query}»: " + " ".join(куски))

    справка = _instant_answer(query)
    if справка:
        return _shorten(f"По запросу «{query}»: {справка}")

    return _open_in_browser(query)

# ─── Объявление для реестра действий ──────────────────────────────────────────
# Инструмент описывает себя сам: имя, текст для модели, схема аргументов и
# обработчик. core/action_loader.py находит это при запуске — ни списка в
# main.py, ни ветки в диспетчере для нового инструмента больше не нужно.
TOOL = {
    "name": "web_search",
    "description": (
        "Ищет информацию в интернете. Вызывай на ЛЮБОЙ вопрос о текущих фактах, "
        "событиях, ценах и людях — всегда предпочитай поиск догадке по памяти. "
        "Режимы: search (по умолчанию) — короткий ответ на вопрос; "
        "news — три самые свежие новости по теме; "
        "research — развёрнутый разбор; "
        "price — актуальная цена с валютой и источником; "
        "compare — сравнение двух и более предметов (перечисли их в items)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING", "description": "Поисковый запрос или тема"},
            "mode": {
                "type": "STRING",
                "description": "search | news | research | price | compare",
            },
            "items": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Что с чем сравнивать (для mode=compare)",
            },
            "aspect": {
                "type": "STRING",
                "description": "Что важнее всего в сравнении: цена, характеристики, отзывы",
            },
        },
        "required": ["query"]
    },
    "handler": web_search,
}
