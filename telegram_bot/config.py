"""Load bot configuration from config/api_keys.json and environment variables."""
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple

BASE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"


class Config(NamedTuple):
    gemini_api_key: str
    telegram_token: str
    allowed_user_ids: list
    pc_ws_host: str
    pc_ws_port: int
    miniapp_url: str
    miniapp_port: int


def load() -> Config:
    raw: dict = {}
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            raw = json.load(f)

    gemini_key = os.getenv("GEMINI_API_KEY") or raw.get("gemini_api_key", "")
    token = os.getenv("TELEGRAM_BOT_TOKEN") or raw.get("telegram_bot_token", "")

    allowed_raw = os.getenv("TELEGRAM_ALLOWED_USERS") or raw.get("telegram_allowed_users", "")
    if isinstance(allowed_raw, list):
        allowed = [int(x) for x in allowed_raw if str(x).strip()]
    else:
        allowed = [int(x.strip()) for x in str(allowed_raw).split(",") if x.strip()]

    pc_host = os.getenv("PC_WS_HOST") or raw.get("pc_ws_host", "")
    pc_port = int(os.getenv("PC_WS_PORT") or raw.get("pc_ws_port", 8765))
    miniapp_url = os.getenv("MINIAPP_URL") or raw.get("miniapp_url", "")
    miniapp_port = int(os.getenv("MINIAPP_PORT") or raw.get("miniapp_port", 8000))

    if not gemini_key:
        print("ERROR: gemini_api_key not found. Set it in config/api_keys.json or GEMINI_API_KEY env.")
        sys.exit(1)
    if not token:
        print("ERROR: telegram_bot_token not found. Set it in config/api_keys.json or TELEGRAM_BOT_TOKEN env.")
        sys.exit(1)

    return Config(
        gemini_api_key=gemini_key,
        telegram_token=token,
        allowed_user_ids=allowed,
        pc_ws_host=pc_host,
        pc_ws_port=pc_port,
        miniapp_url=miniapp_url,
        miniapp_port=miniapp_port,
    )
