"""Футбол на настоящих данных (CI): ESPN находит клуб, отдаёт матчи и табло,
Google Новости — русские заголовки. Формат ответов сверяется с разбором в
core/football.py — отсюда эти сайты закрыты, поэтому проверка в CI.

Запуск: python scripts/football_check.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import football as F  # noqa: E402


def probe() -> None:
    """Что отвечает ESPN как есть: код, начало ответа или ошибка (разбор ниже молчит об ошибках)."""
    import urllib.request
    for url in (f"{F.ESPN}/esp.1/teams", f"{F.ESPN}/esp.1/teams/86/schedule", F.LOGO.format(id=86)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Jarvis/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read()
            print(f"-- {url}\n   {r.status} {r.headers.get('Content-Type')} {len(body)} байт: {body[:300]!r}")
        except Exception as exc:
            print(f"-- {url}\n   ОШИБКА {type(exc).__name__}: {exc}")
    try:
        data = F._json(f"{F.ESPN}/esp.1/teams")
        lg = (data.get("sports") or [{}])[0].get("leagues", [{}])[0]
        teams = [t.get("team", t) for t in lg.get("teams", [])]
        print(f"   команд в лиге: {len(teams)}; первые: {[t.get('displayName') for t in teams[:4]]}")
        print(f"   ключи команды: {sorted(teams[0])[:20] if teams else '—'}")
    except Exception as exc:
        print(f"   разбор: {type(exc).__name__}: {exc}")


def main() -> int:
    import logging
    logging.basicConfig(level=logging.DEBUG, format="   [%(name)s] %(message)s")
    probe()
    ok = True
    for club in ("Реал", "Ливерпуль", "Зенит"):
        with tempfile.TemporaryDirectory() as tmp:
            f = F.Football(Path(tmp) / "f.json")
            print(f"== {club}: {f.set_club(club)}")
            team = f.state["team"]
            print("   ESPN:", team)
            ms = f.fixtures()
            print(f"   матчей: {len(ms)}; прошедших: {sum(m.state == 'post' for m in ms)}")
            for m in ms[-3:]:
                print(f"   {m.when:%Y-%m-%d %H:%M} {m.score()} [{m.state} {m.detail}] {m.league}")
            if ms:
                m = ms[-1]
                print(f"   сокращения/цвета: {m.home_abbr}/{m.home_color} — {m.away_abbr}/{m.away_color}")
                print(f"   эмблема: {m.home_logo}")
                path = F.crest(m.home_logo, root=Path(tmp) / "crests")
                print(f"   эмблема скачана: {path.stat().st_size if path else 'НЕТ'} байт")
                if club == "Реал" and not path:
                    ok = False
            print("  ", f.next_text())
            print("  ", f.last_text())
            news = f.news(limit=3)
            print(f"   новостей: {len(news)}", *(f"\n   - {n.title} ({n.source})" for n in news))
            if club == "Реал" and (not team or not ms or not news):
                ok = False
    print("\nИтог:", "ФОРМАТ СОВПАДАЕТ" if ok else "НЕ СОВПАДАЕТ — смотри выше")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
