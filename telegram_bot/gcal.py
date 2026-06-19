"""Google Calendar — read-only sync of today's / tomorrow's events.

OAuth runs ONCE on the PC (browser consent) via scripts/gcal_login.py, which
writes config/token.json holding a refresh token. After that this module
refreshes access tokens silently and lists events. Credentials never touch the
HF server — calendar runs PC-side (where the token lives) and is surfaced to
JARVIS through the bridge / [[FETCH]] chain.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("jarvis-gcal")

_CONFIG = Path(__file__).resolve().parent.parent / "config"
_CRED = _CONFIG / "credentials.json"
_TOKEN = _CONFIG / "token.json"

# Read-only — JARVIS reads the calendar, never modifies it.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def is_configured() -> bool:
    """True once OAuth has produced a token — either the local file (PC) or the
    GCAL_TOKEN_JSON env var (HF, where there's no browser to log in)."""
    return _TOKEN.exists() or bool(os.getenv("GCAL_TOKEN_JSON", "").strip())


def _load_credentials():
    import json
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    # token.json (from InstalledAppFlow) already embeds client_id/secret +
    # refresh_token, so no credentials.json is needed to refresh — HF only needs
    # the token contents in the GCAL_TOKEN_JSON secret.
    env_token = os.getenv("GCAL_TOKEN_JSON", "").strip()
    if _TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN), SCOPES)
    elif env_token:
        creds = Credentials.from_authorized_user_info(json.loads(env_token), SCOPES)
    else:
        return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            if _TOKEN.exists():            # only persist where we own the file (PC)
                _TOKEN.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            logger.error(f"token refresh failed: {e}")
            return None
    return creds


def _fmt_event(ev: dict, tz_name: str) -> dict:
    start = ev.get("start", {})
    # all-day events use 'date'; timed events use 'dateTime'
    when = start.get("dateTime") or start.get("date") or ""
    time_label = ""
    if "dateTime" in start:
        try:
            dt = datetime.fromisoformat(start["dateTime"])
            time_label = dt.strftime("%H:%M")
        except Exception:
            time_label = ""
    return {
        "summary": ev.get("summary", "(без названия)"),
        "time": time_label,
        "location": ev.get("location", ""),
        "when": when,
    }


def list_events(day_offset_start: int = 0, day_offset_end: int = 1,
                tz_name: str = "Asia/Almaty", max_results: int = 20) -> list:
    """Return events between [today+start, today+end] inclusive. Empty list on
    any failure (never raises — calendar is a nice-to-have, not a hard dep)."""
    try:
        creds = _load_credentials()
        if not creds:
            return []
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)

        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone(timedelta(hours=5))

        now = datetime.now(tz)
        start = (now + timedelta(days=day_offset_start)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (now + timedelta(days=day_offset_end)).replace(hour=23, minute=59, second=59, microsecond=0)

        result = service.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        ).execute()
        return [_fmt_event(ev, tz_name) for ev in result.get("items", [])]
    except Exception as e:
        logger.error(f"list_events failed: {e}")
        return []


def events_text(events: list) -> str:
    """Human one-liner list for a digest/context, or '' if empty."""
    if not events:
        return ""
    parts = []
    for e in events:
        s = (f"{e['time']} " if e["time"] else "") + e["summary"]
        if e["location"]:
            s += f" ({e['location']})"
        parts.append(s)
    return "; ".join(parts)
