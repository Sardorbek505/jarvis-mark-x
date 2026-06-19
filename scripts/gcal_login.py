#!/usr/bin/env python3
"""One-time Google Calendar login (run ONCE on the PC, where a browser exists).

  python scripts/gcal_login.py

Opens a browser for consent, then writes config/token.json (holds the refresh
token). After this, telegram_bot/gcal.py reads the calendar silently. Re-run
only if the token is revoked or you switch Google accounts.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telegram_bot import gcal  # noqa: E402


def main() -> int:
    if not gcal._CRED.exists():
        print(f"[ERROR] Не найден {gcal._CRED}.")
        print("        Положи туда credentials.json (OAuth Desktop client) из Google Cloud.")
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("Открываю браузер для входа в Google… разреши доступ к календарю (только чтение).")
    flow = InstalledAppFlow.from_client_secrets_file(str(gcal._CRED), gcal.SCOPES)
    creds = flow.run_local_server(port=0)
    gcal._TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print(f"[OK] Токен сохранён в {gcal._TOKEN}")

    # Smoke test: list the next events so the user sees it works.
    events = gcal.list_events(0, 7)
    print(f"[OK] Календарь подключён. Событий на ближайшую неделю: {len(events)}")
    for e in events[:5]:
        line = (f"{e['time']} " if e['time'] else "") + e['summary']
        print("   •", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
