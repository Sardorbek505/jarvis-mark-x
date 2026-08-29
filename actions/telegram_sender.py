"""ДЖАРВИС — Голосовая отправка сообщений и скриншотов в Telegram.

Позволяет Джарвису голосом отправлять пользователю в телефон заметки, ссылки,
напоминания и текущие скриншоты экрана.
"""
import logging
import os
import urllib.parse
import urllib.request
from typing import Optional

from core.paths import load_api_keys

logger = logging.getLogger("jarvis-telegram-sender")


def _get_tg_config() -> tuple[str, list[int]]:
    """Возвращает (bot_token, [allowed_user_ids])."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    users = []
    cfg = load_api_keys()
    if not token:
        token = cfg.get("telegram_bot_token", "").strip()
    raw_users = cfg.get("telegram_allowed_users", [])
    if isinstance(raw_users, list):
        users = [int(u) for u in raw_users if str(u).isdigit()]
    elif str(raw_users).isdigit():
        users = [int(raw_users)]
    return token, users


def send_to_telegram(
    text: Optional[str] = None,
    send_screenshot: bool = False,
) -> str:
    """Отправляет текстовое сообщение или скриншот в Telegram пользователя.

    Аргументы:
        text: Текст сообщения или подпись к скриншоту.
        send_screenshot: Если True, делает снимок экрана и отправляет его.
    """
    token, users = _get_tg_config()
    if not token or not users:
        return "Сэр, связь с Telegram не настроена. Укажите токен бота и User ID в окне настроек."

    target_chat_id = users[0]

    try:
        if send_screenshot:
            from actions.vision import capture_screen_jpeg
            jpeg_data = capture_screen_jpeg()
            if not jpeg_data:
                return "Сэр, не удалось захватить экран для отправки."

            # Отправка фото через multipart/form-data
            boundary = "----JarvisTelegramBoundary"
            body = bytearray()

            # chat_id
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{target_chat_id}\r\n'.encode("utf-8"))

            # caption
            caption = text or "📸 Снимок экрана от Джарвиса"
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode("utf-8"))

            # photo file
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend('Content-Disposition: form-data; name="photo"; filename="screenshot.jpg"\r\n'.encode("utf-8"))
            body.extend(b"Content-Type: image/jpeg\r\n\r\n")
            body.extend(jpeg_data)
            body.extend(b"\r\n")
            body.extend(f"--{boundary}--\r\n".encode("utf-8"))

            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            req = urllib.request.Request(
                url,
                data=bytes(body),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            urllib.request.urlopen(req, timeout=15)
            return "Сэр, снимок экрана успешно отправлен в ваш Telegram."

        else:
            # Текстовое сообщение
            msg_text = text or "Привет от Джарвиса!"
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": target_chat_id, "text": msg_text}).encode("utf-8")
            req = urllib.request.Request(url, data=data)
            urllib.request.urlopen(req, timeout=10)
            return "Сэр, сообщение успешно отправлено в ваш Telegram."

    except Exception as e:
        logger.error("Failed to send telegram message: %s", e)
        return f"Сэр, произошла ошибка при отправке в Telegram: {e}"


def telegram_sender_action(params: dict) -> str:
    """Точка входа для инструментов."""
    text = params.get("text") or params.get("message") or ""
    send_screen = params.get("send_screenshot", False) or "скриншот" in text.lower() or "экран" in text.lower()
    return send_to_telegram(text=text, send_screenshot=send_screen)
