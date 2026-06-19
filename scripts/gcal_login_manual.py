#!/usr/bin/env python3
"""Robust Google Calendar login via a printed URL + fixed-port loopback catcher.

Does NOT depend on a browser auto-opening (that failed in the headless session).
Writes the auth URL to scratch so it can be relayed to the user, runs a tiny
local server on a fixed port to catch the redirect, exchanges the code, and
saves config/token.json.

  python scripts/gcal_login_manual.py
"""
import http.server
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telegram_bot import gcal  # noqa: E402

PORT = 53682  # loopback redirect port (Desktop OAuth clients allow any localhost port)
URL_FILE = ROOT / "config" / "gcal_auth_url.txt"


def main() -> int:
    if not gcal._CRED.exists():
        print(f"[ERROR] нет {gcal._CRED}")
        return 1

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(
        str(gcal._CRED), scopes=gcal.SCOPES,
        redirect_uri=f"http://localhost:{PORT}",
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    URL_FILE.write_text(auth_url, encoding="utf-8")
    print("OPEN THIS URL IN A BROWSER ON THIS PC:\n", auth_url, flush=True)

    holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            # Only capture the real OAuth redirect (has code/error); ignore
            # favicon and other stray requests so we don't exit early.
            if "code" in params or "error" in params:
                holder["code"] = params.get("code", [None])[0]
                holder["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = ("Gotovo! Mozhno zakryt vkladku i vernutsya v Telegram."
                   if holder.get("code") else "Zhdu avtorizaciyu...")
            self.wfile.write(f"<h2>{msg}</h2>".encode("utf-8"))

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("localhost", PORT), Handler)
    srv.timeout = 900  # 15 min per wait — survive a slow login
    # Keep serving until we actually get a code/error (favicon etc. won't end it).
    while "code" not in holder and "error" not in holder:
        srv.handle_request()

    if holder.get("error") or not holder.get("code"):
        print(f"[ERROR] авторизация не завершена: {holder.get('error')}")
        return 2

    flow.fetch_token(code=holder["code"])
    gcal._TOKEN.write_text(flow.credentials.to_json(), encoding="utf-8")
    URL_FILE.unlink(missing_ok=True)
    print(f"[OK] токен сохранён в {gcal._TOKEN}", flush=True)

    events = gcal.list_events(0, 7)
    print(f"[OK] календарь подключён. событий за неделю: {len(events)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
