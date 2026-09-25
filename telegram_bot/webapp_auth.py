"""Проверка подписи данных Telegram Mini App (initData). Без внешних зависимостей."""


def verify_init_data(init_data: str, bot_token: str, max_age_sec: int = 7 * 86400) -> int | None:
    """id пользователя из initData Telegram, если подпись верна; иначе None.

    Раньше user_id брался из строки запроса как есть: любой, кто знал адрес
    Space, открывал /ws?user_id=<id владельца> и читал его факты, задачи и
    напоминания, писал от его имени и командовал его ПК.
    Проверка — по документации Telegram Mini Apps: HMAC-SHA256 с ключом
    HMAC_SHA256("WebAppData", bot_token).
    """
    import hashlib
    import hmac
    import json as _json
    import time as _time
    from urllib.parse import parse_qsl
    if not init_data or not bot_token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        return None
    try:
        if _time.time() - int(pairs.get("auth_date", "0")) > max_age_sec:
            return None
        return int(_json.loads(pairs.get("user", "{}")).get("id"))
    except (ValueError, TypeError, AttributeError):
        return None
