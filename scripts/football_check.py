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


def main() -> int:
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
