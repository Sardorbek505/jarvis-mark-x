"""Load bot configuration from config/api_keys.json and environment variables."""
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple

BASE_DIR = Path(__file__).resolve().parent.parent
_CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"

# Боевой деплой ровно один — Hugging Face Space. На Render остался живой дубль
# (05.08.2026), и два инстанса каждые 90 с перетягивали вебхук друг у друга:
# часть апдейтов уходила в дубль, у которого нет связи с ПК. Отсюда хроническое
# «бот то отвечает, то молчит».
DECOMMISSIONED_HOSTS = ("onrender.com",)


def is_decommissioned(miniapp_url: str) -> bool:
    """Узнаёт выведенный из эксплуатации деплой по его собственному адресу.
    Такой инстанс не должен трогать вебхук — иначе отбирает апдейты у рабочего."""
    url = (miniapp_url or "").lower()
    return any(host in url for host in DECOMMISSIONED_HOSTS)


class Config(NamedTuple):
    gemini_api_key: str
    gemini_model: str
    telegram_token: str
    allowed_user_ids: list
    pc_ws_host: str
    pc_ws_port: int
    miniapp_url: str
    miniapp_port: int
    pc_link_url: str
    pc_link_token: str
    default_city: str
    timezone: str


def load(require_bot: bool = True) -> Config:
    """Load config.

    require_bot=True  → bot/server needs gemini key + telegram token (exits if missing).
    require_bot=False → PC client mode; only pc_link_* matter (no hard requirements).
    """
    raw: dict = {}
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            raw = json.load(f)

    gemini_key = (os.getenv("GEMINI_API_KEY") or raw.get("gemini_api_key", "")).strip()
    gemini_model = (os.getenv("GEMINI_MODEL") or raw.get("gemini_model", "gemini-2.5-flash")).strip()
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or raw.get("telegram_bot_token", "")).strip()

    allowed_raw = os.getenv("TELEGRAM_ALLOWED_USERS") or raw.get("telegram_allowed_users", "")
    if isinstance(allowed_raw, list):
        allowed = [int(x) for x in allowed_raw if str(x).strip()]
    else:
        allowed = [int(x.strip()) for x in str(allowed_raw).split(",") if x.strip()]

    # Защита от Fail-Open: если список пуст, подгружаем ID владельца из профиля
    if not allowed:
        profile_path = BASE_DIR / "config" / "user_profile.json"
        if profile_path.exists():
            try:
                with open(profile_path, encoding="utf-8") as pf:
                    pdata = json.load(pf)
                    tid = pdata.get("telegram_id")
                    if tid:
                        allowed = [int(tid)]
            except Exception:
                pass

    pc_host = os.getenv("PC_WS_HOST") or raw.get("pc_ws_host", "")
    pc_port = int(os.getenv("PC_WS_PORT") or raw.get("pc_ws_port", 8765))
    # Render automatically provides RENDER_EXTERNAL_URL — use it so the user
    # never has to set MINIAPP_URL by hand (fixes "Mini App не настроен").
    render_url = os.getenv("RENDER_EXTERNAL_URL", "")
    miniapp_url = os.getenv("MINIAPP_URL") or raw.get("miniapp_url", "") or render_url
    miniapp_port = int(os.getenv("MINIAPP_PORT") or raw.get("miniapp_port", 8000))

    # PC link — home PC dials out to the server (works behind NAT)
    pc_link_url = (os.getenv("PC_LINK_URL") or raw.get("pc_link_url", "") or miniapp_url).strip()
    pc_link_token = (os.getenv("PC_LINK_TOKEN") or raw.get("pc_link_token", "")).strip()

    # Fallback location/timezone when the phone hasn't reported its own.
    # Defaults tuned for the owner (Shymkent, Kazakhstan, UTC+5).
    default_city = os.getenv("DEFAULT_CITY") or raw.get("default_city", "Шымкент")
    timezone = os.getenv("TIMEZONE") or raw.get("timezone", "Asia/Almaty")

    if require_bot:
        if not gemini_key:
            print("ERROR: gemini_api_key not found. Set it in config/api_keys.json or GEMINI_API_KEY env.")
            sys.exit(1)
        if not token:
            print("ERROR: telegram_bot_token not found. Set it in config/api_keys.json or TELEGRAM_BOT_TOKEN env.")
            sys.exit(1)

    return Config(
        gemini_api_key=gemini_key,
        gemini_model=gemini_model,
        telegram_token=token,
        allowed_user_ids=allowed,
        pc_ws_host=pc_host,
        pc_ws_port=pc_port,
        miniapp_url=miniapp_url,
        miniapp_port=miniapp_port,
        pc_link_url=pc_link_url,
        pc_link_token=pc_link_token,
        default_city=default_city,
        timezone=timezone,
    )
