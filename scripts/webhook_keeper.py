"""
JARVIS — Webhook Keeper (страховка со стороны ПК).

Зачем: HF Spaces (free) периодически рестартует/пересобирается. На graceful
shutdown бот сам делает `delete_webhook` (см. render_app.py), а на старте не
всегда может переустановить вебхук — исходящие к api.telegram.org на HF
блокируются и идут через Cloudflare-прокси, который иногда сбоит. Итог:
вебхук слетает, бот «молчит» в Telegram.

Этот демон крутится на ПК пользователя (egress к Telegram открыт напрямую) и
держит вебхук живым: раз в INTERVAL проверяет getWebhookInfo и переустанавливает,
если URL пуст / не тот / с ошибкой доставки. Secret-token вычисляется ровно так
же, как в render_app.py, иначе бот отвергнет апдейты.

Запуск:
    python scripts/webhook_keeper.py --once   # одна проверка (для планировщика)
    python scripts/webhook_keeper.py          # бесконечный цикл (для автозапуска)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_CONFIG_FILE = _BASE / "config" / "api_keys.json"
_LOG_FILE = _BASE / "logs" / "webhook_keeper.log"

_WEBHOOK_PATH = "/telegram-webhook"
_ALLOWED_UPDATES = ["message", "edited_message", "callback_query"]
_DEFAULT_HF_HOST = "atabekovch-jarvis-mark-x.hf.space"
_INTERVAL_SEC = 300
_API = "https://api.telegram.org"
_TIMEOUT = 25

_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
_logger = logging.getLogger("webhook_keeper")


def _load() -> tuple[str, str, str]:
    """Возвращает (token, webhook_url, secret). secret идентичен render_app.py."""
    cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    token = cfg.get("telegram_bot_token", "").strip()
    if not token:
        raise SystemExit("Нет telegram_bot_token в config/api_keys.json")

    host_src = cfg.get("miniapp_url") or cfg.get("pc_link_url") or ""
    match = re.search(r"([A-Za-z0-9-]+\.hf\.space)", host_src)
    host = match.group(1) if match else _DEFAULT_HF_HOST

    webhook_url = f"https://{host}{_WEBHOOK_PATH}"
    secret = re.sub(r"[^A-Za-z0-9_-]", "", token)[:256]  # == render_app.py:49
    return token, webhook_url, secret


def _api_call(token: str, method: str, params: dict | None = None) -> dict:
    url = f"{_API}/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(url, data=data, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _set_webhook(token: str, webhook_url: str, secret: str) -> None:
    _api_call(
        token,
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": secret,
            "allowed_updates": json.dumps(_ALLOWED_UPDATES),
            "max_connections": "40",
            "drop_pending_updates": "false",
        },
    )


def ensure_once() -> bool:
    """Одна проверка. Возвращает True, если пришлось переустановить вебхук."""
    try:
        token, webhook_url, secret = _load()
        info = _api_call(token, "getWebhookInfo").get("result", {})
        current = info.get("url", "")
        last_error = info.get("last_error_message")

        needs_reset = current != webhook_url or bool(last_error)
        if not needs_reset:
            _logger.info("OK — вебхук на месте (%s)", webhook_url)
            return False

        reason = "пуст/не тот" if current != webhook_url else f"ошибка доставки: {last_error}"
        _logger.warning("Вебхук требует переустановки (%s, было: '%s')", reason, current)
        _set_webhook(token, webhook_url, secret)
        after = _api_call(token, "getWebhookInfo").get("result", {})
        if after.get("url") == webhook_url:
            _logger.warning("Вебхук восстановлен ✅ → %s", webhook_url)
            return True
        _logger.error("Переустановил, но getWebhookInfo всё ещё '%s'", after.get("url"))
        return True
    except urllib.error.URLError as e:
        _logger.warning("Сеть недоступна (%s) — пропускаю цикл", e)
        return False
    except Exception as e:  # noqa: BLE001 — демон не должен падать
        _logger.error("Сбой проверки: %s: %s", type(e).__name__, e)
        return False


def _loop(interval: int) -> None:
    _logger.info("Webhook Keeper запущен (интервал %d с)", interval)
    while True:
        ensure_once()
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="JARVIS Telegram webhook keeper")
    parser.add_argument("--once", action="store_true", help="одна проверка и выход")
    parser.add_argument("--interval", type=int, default=_INTERVAL_SEC, help="секунды между проверками")
    args = parser.parse_args()

    if args.once:
        ensure_once()
    else:
        _loop(args.interval)


if __name__ == "__main__":
    main()
