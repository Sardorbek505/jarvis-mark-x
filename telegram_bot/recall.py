"""In-chat semantic recall (item #8).

When the user asks "где я записывал про X", "что я говорил о Y", "помнишь про…"
JARVIS should answer from their notes/facts WITHOUT needing the /findnote
command. This module detects recall-intent and pulls matching notes (and cached
facts) so they can be injected into the prompt as extra context — one cheap DB
query, no extra Gemini call.
"""
import re

import logging

_logger = logging.getLogger(__name__)

# Phrases that signal the user is asking JARVIS to remember/look something up.
_TRIGGERS = (
    "где я", "где это", "что я", "что мы", "помнишь", "напомни что",
    "записыва", "говорил", "говорили", "обсуждали", "о чём", "про что",
    "найди", "вспомни", "что было про", "что насчёт", "что там про",
    "я тебе говорил", "я писал", "я записал", "у меня было",
)

# Low-value words to drop when extracting search keywords.
_STOP = {
    "где", "что", "как", "кто", "когда", "почему", "зачем", "чём", "чем",
    "это", "этом", "том", "про", "для", "над", "под", "при", "без",
    "мне", "меня", "мой", "моя", "моё", "мои", "тебе", "тебя", "себе",
    "я", "ты", "мы", "вы", "он", "она", "они", "и", "а", "но", "или",
    "был", "была", "было", "были", "есть", "буду", "будет",
    "помнишь", "вспомни", "найди", "говорил", "говорили", "записывал",
    "писал", "записал", "обсуждали", "насчёт", "там", "тут",
}

_WORD = re.compile(r"[а-яёa-z0-9]+", re.I)


def looks_like_recall(text: str) -> bool:
    low = text.lower()
    return any(t in low for t in _TRIGGERS)


def _keywords(text: str) -> list:
    words = [w.lower() for w in _WORD.findall(text)]
    seen, out = set(), []
    for w in words:
        if len(w) < 4 or w in _STOP or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:6]


async def search(memory, uid: int, text: str) -> str:
    """Return an extra-context string of matching notes/facts, or '' if the
    message isn't a recall query or nothing matched."""
    if not memory or not looks_like_recall(text):
        return ""

    keywords = _keywords(text)
    if not keywords:
        return ""

    hits, seen = [], set()
    for kw in keywords:
        try:
            for note in await memory.search_notes(uid, kw):
                t = note["text"].strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    hits.append(t)
        except Exception as exc:
            _logger.debug("Подавлено исключение: %s", exc, exc_info=True)
            continue

    # also scan facts already in the RAM cache (free, synchronous)
    try:
        facts = memory.cached_facts(uid) if hasattr(memory, "cached_facts") else []
    except Exception:
        facts = []
    for f in facts:
        fl = f.lower()
        if any(kw in fl for kw in keywords) and fl not in seen:
            seen.add(fl)
            hits.append(f)

    if not hits:
        return ("ПОИСК ПО ЗАПИСЯМ: по этому вопросу ничего не найдено в заметках "
                "пользователя — честно скажи, что записи на эту тему нет.")
    found = "; ".join(hits[:8])
    return ("НАЙДЕНО В ЗАПИСЯХ ПОЛЬЗОВАТЕЛЯ ПО ЭТОМУ ВОПРОСУ (ответь, опираясь на это): "
            + found)
