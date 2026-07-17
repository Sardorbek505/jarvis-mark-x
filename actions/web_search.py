"""
Действие: веб-поиск через DuckDuckGo (без API-ключа)
"""

import urllib.request
import urllib.parse
import json


def web_search(parameters: dict, player=None) -> str:
    query = parameters.get("query", "").strip()
    if not query:
        return "Укажите поисковый запрос."

    try:
        # DuckDuckGo Instant Answer API
        url = "https://api.duckduckgo.com/?q=" + urllib.parse.quote(query) + "&format=json&no_redirect=1&no_html=1"
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        # Пробуем разные поля ответа
        answer = data.get("AbstractText", "").strip()
        if not answer:
            answer = data.get("Answer", "").strip()
        if not answer and data.get("RelatedTopics"):
            topics = data["RelatedTopics"]
            snippets = []
            for t in topics[:3]:
                if isinstance(t, dict) and t.get("Text"):
                    snippets.append(t["Text"])
            answer = " | ".join(snippets)

        if answer:
            # Ограничиваем длину
            answer = answer[:400]
            if player:
                player.write_log(f"SYS: Поиск — {query}")
            return f"По запросу «{query}»: {answer}"
        else:
            # Открываем браузер как fallback
            import subprocess, sys
            encoded = urllib.parse.quote(query)
            url_browser = f"https://duckduckgo.com/?q={encoded}"
            if sys.platform == "win32":
                import os; os.startfile(url_browser)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", url_browser])
            else:
                subprocess.Popen(["xdg-open", url_browser])
            return f"Открыл поиск по запросу «{query}» в браузере."

    except Exception as e:
        return f"Ошибка поиска: {e}"
