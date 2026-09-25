"""Mini App: доступ только по подписанным Telegram данным."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from telegram_bot.miniapp_server import verify_init_data

TOKEN = "123456:TEST-token"


def _signed(user_id: int, auth_date: int | None = None) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "S"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_signature_gives_user_id():
    assert verify_init_data(_signed(42), TOKEN) == 42


def test_forged_or_foreign_data_is_rejected():
    forged = _signed(42).replace("%22id%22%3A+42", "%22id%22%3A+1")
    assert verify_init_data(forged, TOKEN) is None
    assert verify_init_data(_signed(42), "other:token") is None
    assert verify_init_data("", TOKEN) is None
    assert verify_init_data("user_id=42", TOKEN) is None


def test_stale_data_is_rejected():
    assert verify_init_data(_signed(42, auth_date=int(time.time()) - 30 * 86400), TOKEN) is None
