"""
Desktop userbot (Telethon) — отправляет сообщения Telegram от имени пользователя.

Крутится на домашнем ПК: тут egress к Telegram открыт (в отличие от HF), а
ключ-сессия остаётся локально. HF-бот шлёт через PC-link команду
{action:"send_telegram", target, text, as_voice}; этот модуль доставляет её
текстом или синтезированным голосовым (Gemini Charon → OGG/Opus).

Первый вход (один раз, в обычном терминале — интерактивно спросит номер и код):
    python -m telegram_bot.pc_userbot login
"""
import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeAudio

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
SESSION_PATH = str(BASE_DIR / "config" / "userbot")   # Telethon добавит .session
_CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"

_client: "TelegramClient | None" = None
_lock = asyncio.Lock()


def _credentials() -> tuple[int, str]:
    raw: dict = {}
    if _CONFIG_FILE.exists():
        raw = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    api_id = os.getenv("TELETHON_API_ID") or raw.get("telethon_api_id")
    api_hash = os.getenv("TELETHON_API_HASH") or raw.get("telethon_api_hash")
    if not api_id or not api_hash:
        raise RuntimeError(
            "telethon_api_id / telethon_api_hash не заданы в config/api_keys.json"
        )
    return int(api_id), str(api_hash)


async def get_client() -> "TelegramClient | None":
    """Подключённый и авторизованный Telethon-клиент (или None, если не залогинен)."""
    global _client
    async with _lock:
        if _client and _client.is_connected():
            return _client
        api_id, api_hash = _credentials()
        _client = TelegramClient(SESSION_PATH, api_id, api_hash)
        await _client.connect()
        if not await _client.is_user_authorized():
            logger.error(
                "Userbot не авторизован — выполни: python -m telegram_bot.pc_userbot login"
            )
            await _client.disconnect()
            _client = None
            return None
        return _client


def _ogg_duration(path: str) -> int:
    """Длительность OGG в секундах (для корректного voice note)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return max(1, round(float(out.stdout.strip())))
    except Exception:
        return 1


async def send_message(target: str, text: str, as_voice: bool = False, gemini=None) -> dict:
    """Отправить текст или голосовое контакту `target` (@username / телефон / id).
    Возвращает {"ok": bool, "error": str|None}."""
    client = await get_client()
    if client is None:
        return {"ok": False, "error": "userbot не авторизован (нужен вход)"}

    try:
        entity = await client.get_entity(target)
    except Exception as e:
        return {"ok": False, "error": f"контакт не найден ({target}): {e}"}

    try:
        if as_voice and gemini is not None:
            ogg = await gemini.speak_ogg(text)
            if not ogg:
                await client.send_message(entity, text)   # синтез не вышел → текстом
                return {"ok": True, "error": "голос недоступен — отправил текстом"}
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
                    tf.write(ogg)
                    tmp = tf.name
                await client.send_file(
                    entity, tmp, voice_note=True,
                    attributes=[DocumentAttributeAudio(duration=_ogg_duration(tmp), voice=True)],
                )
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
        else:
            await client.send_message(entity, text)
        return {"ok": True, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _login():
    api_id, api_hash = _credentials()
    client = TelegramClient(SESSION_PATH, api_id, api_hash)
    await client.start()   # интерактивно: номер → код → 2FA-пароль (если есть)
    me = await client.get_me()
    uname = f"@{me.username}" if me.username else me.id
    print(f"\n✅ Userbot вошёл как: {me.first_name} ({uname})")
    print(f"Сессия сохранена: {SESSION_PATH}.session — больше код вводить не нужно.")
    await client.disconnect()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        asyncio.run(_login())
    else:
        print("Использование: python -m telegram_bot.pc_userbot login")
