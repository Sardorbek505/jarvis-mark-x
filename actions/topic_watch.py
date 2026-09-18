"""
Действие: следить за темой и сказать, когда появится новое.

Вся механика — в `core/watcher.py`: список тем, фоновый поток, правило «что
считается новым» и границы, за которыми полезное становится невыносимым.
Здесь только разбор того, что сказал человек, и объявление для модели.
"""

from core import watcher

_СНЯТЬ = ("stop", "remove", "off", "cancel", "убрать", "снять", "перестань", "хватит")
_СПИСОК = ("list", "status", "список", "что")
_СЕЙЧАС = ("check", "now", "проверь")


def topic_watch(parameters: dict, player=None) -> str:
    параметры = parameters or {}
    действие = str(параметры.get("action", "add")).strip().lower()
    тема = str(параметры.get("topic", "")).strip()
    режим = str(параметры.get("mode", "news")).strip().lower()

    интервал = параметры.get("interval_minutes")
    try:
        интервал = int(интервал) if интервал else None
    except (TypeError, ValueError):
        интервал = None

    if any(действие.startswith(с) for с in _СНЯТЬ):
        return watcher.remove(тема)

    if действие in _СПИСОК:
        return watcher.describe()

    if действие in _СЕЙЧАС:
        записи = watcher.topics()
        if not записи:
            return "Я ни за чем не слежу, сэр."
        цели = [з for з in записи if not тема or тема.lower() in з["тема"].lower()]
        if not цели:
            return f"За «{тема}» я не слежу, сэр."
        # Проверка по просьбе идёт и в тихие часы: молчание ночью — про то,
        # чтобы не заговаривать самому, а не про то, чтобы не отвечать.
        новости = []
        for запись in цели:
            новости.extend(watcher.проверить_тему(запись))
        if not новости:
            return "Нового по теме пока нет, сэр."
        return " ".join(новости)

    if player:
        player.write_log(f"SYS: слежение — {тема or 'без темы'}")
    return watcher.add(тема, режим, интервал)


# ─── Объявление для реестра действий ──────────────────────────────────────────
TOOL = {
    "name": "topic_watch",
    "description": (
        "Следит за темой и сам сообщает, когда по ней появляется новое. "
        "Вызывай, когда пользователь говорит: следи за…, дай знать, когда…, "
        "сообщи, если появится…, держи меня в курсе по…. "
        "action=add — взять тему под наблюдение (сразу отвечает, что по ней "
        "сейчас); action=list — за чем слежу; action=check — проверить сейчас; "
        "action=stop — перестать (без topic — снять всё). "
        "Это слежение за СОБЫТИЯМИ: вышло, объявили, отменили, подорожало. "
        "Мелкие колебания числа — курс с точностью до копейки — так не "
        "отслеживаются, и обещать этого не нужно. "
        "Разовый вопрос «что нового» — это web_search, а не слежение."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "add (по умолчанию) | list | check | stop",
            },
            "topic": {
                "type": "STRING",
                "description": "За чем следить: «выход GTA 6», «новости про Байкал»",
            },
            "mode": {
                "type": "STRING",
                "description": "Как проверять: news (по умолчанию) | search | price",
            },
            "interval_minutes": {
                "type": "INTEGER",
                "description": "Как часто проверять; минимум 15, по умолчанию 30",
            },
        },
        "required": [],
    },
    "handler": topic_watch,
}
