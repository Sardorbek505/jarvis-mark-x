"""Ключи и подключения: какие сервисы есть, что каждый даёт, где взять
ключ и работает ли он НА САМОМ ДЕЛЕ.

Раньше ключи лежали в config/api_keys.json, и понять, какой из них
сломался, можно было только по странному поведению Джарвиса. Теперь у
каждого сервиса есть проверка — настоящий запрос к нему, — и ответ
человеческими словами: «ключ неверный», «квота на сегодня кончилась»,
«ключи верные, осталось войти по QR».

Ключи хранятся только на этом ПК (core.paths.save_api_keys) и никуда не
пишутся целиком: в окне и журнале — только последние 4 символа.
Окно — ui_keys.py.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

TIMEOUT = 8
# Своё имя в запросах: стандартное «Python-urllib/3.x» защита Cloudflare (Groq и др.)
# отбивает кодом 403 ещё до сервиса — и верный ключ выглядел «неверным».
USER_AGENT = "JARVIS-Mark-X/1.1 (+https://github.com/Sardorbek505/jarvis-mark-x)"
# При копировании из браузера к ключу цепляются пробелы, переносы и невидимые символы.
# Секрет связи придумывает сам человек — он должен совпасть с сервером буква в букву.
_AS_TYPED = {"pc_link_token"}
_JUNK = re.compile(r"[\s\u200b-\u200f\u2060\ufeff\u00a0]+")


@dataclass
class Field:
    key: str                      # имя в api_keys.json
    label: str
    secret: bool = True
    placeholder: str = ""
    env: str = ""                 # переменная среды, которая его перекрывает
    optional: bool = False


@dataclass
class Service:
    id: str
    title: str
    icon: str
    gives: str                    # что даёт — одной строкой
    fields: list[Field]
    steps: list[str]              # где взять — по шагам
    url: str = ""
    required: bool = False
    check: Callable[[dict], tuple[str, str]] | None = None     # → ("ok"|"warn"|"bad", текст)
    action: str = ""              # кнопка входа: "spotify" | "caller"
    extra: dict = field(default_factory=dict)


# ── запросы ───────────────────────────────────────────────────────────────────

def _http(url: str, headers: dict | None = None, data: bytes | None = None, method: str | None = None):
    """(код, тело) — без исключений на 4xx/5xx."""
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode("utf-8", "replace")


def clean_key(value: str) -> str:
    """Ключ без мусора от копирования: пробелов, переносов, невидимых символов, кавычек."""
    return _JUNK.sub("", str(value or "")).strip("\"'«»")


def _api_message(body: str) -> str:
    """Текст ошибки из JSON-ответа сервиса (Google, OpenAI-совместимые)."""
    try:
        err = json.loads(body).get("error", {})
        return str(err.get("message", "") if isinstance(err, dict) else err)[:160]
    except (ValueError, AttributeError):
        return ""


def _net_error(exc: Exception) -> tuple[str, str]:
    return "warn", f"Не получилось проверить — нет связи ({type(exc).__name__}). Ключ сохранён."


def check_gemini(v: dict) -> tuple[str, str]:
    # Формат не угадываем: кроме старых «AIza…» (39 символов) Google выдаёт ключи и
    # другого вида — раньше такие отбивались как «не похожие», даже не дойдя до Google.
    key = clean_key(v.get("gemini_api_key", ""))
    if len(key) < 20:
        return "bad", "Ключ слишком короткий — скопируйте его в Google AI Studio целиком."
    try:
        code, body = _http("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
                           {"x-goog-api-key": key})
    except Exception as exc:
        return _net_error(exc)
    if code == 200:
        return "ok", "Ключ работает."
    if code == 429 or "RESOURCE_EXHAUSTED" in body:
        return "warn", "Ключ верный, но квота на сегодня кончилась — заработает завтра или с новым ключом."
    if "API_KEY_INVALID" in body or code == 401:
        return "bad", "Ключ неверный — Google его не принимает."
    if code == 403:
        why = _api_message(body)
        if "SERVICE_DISABLED" in body or "has not been used" in why:
            return "bad", "В проекте ключа выключен Gemini API — создайте ключ в Google AI Studio."
        return "bad", "Ключ отключён или заблокирован — создайте новый в Google AI Studio." + (
            f" ({why})" if why else "")
    return "warn", f"Google ответил ошибкой {code}, ключ сохранён. {_api_message(body)}".strip()


def check_fish(v: dict) -> tuple[str, str]:
    key = v.get("fish_api_key", "").strip()
    if len(key) < 16:
        return "bad", "Не похоже на ключ Fish Audio — скопируйте его целиком."
    head = {"Authorization": f"Bearer {key}"}
    try:
        code, body = _http("https://api.fish.audio/wallet/self/api-credit", head)
        if code == 404:                                   # старый адрес — проверим голосом
            voice = v.get("fish_voice_id", "").strip() or "680d74fbef69419f87cfc70f092a1451"
            code, body = _http(f"https://api.fish.audio/model/{urllib.parse.quote(voice)}", head)
    except Exception as exc:
        return _net_error(exc)
    if code == 200:
        try:
            credit = json.loads(body).get("credit")
        except ValueError:
            credit = None
        return "ok", "Ключ работает." + (f" Баланс: {credit}." if credit not in (None, "") else "")
    if code in (401, 403):
        return "bad", "Ключ неверный — Fish Audio его не принимает."
    if code == 402:
        return "warn", "Ключ верный, но на счету Fish Audio закончились деньги — голос будет Edge."
    return "bad", f"Fish Audio ответил ошибкой {code}."


def check_spotify(v: dict) -> tuple[str, str]:
    cid, sec = v.get("spotify_client_id", "").strip(), v.get("spotify_client_secret", "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", cid) or not re.fullmatch(r"[0-9a-f]{32}", sec):
        return "bad", "Client ID и Client Secret — по 32 символа (цифры и a–f). Скопируйте из панели Spotify."
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    try:
        code, _ = _http("https://accounts.spotify.com/api/token", {
            "Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
            b"grant_type=client_credentials", "POST")
    except Exception as exc:
        return _net_error(exc)
    if code != 200:
        return "bad", "Spotify не принял Client ID / Secret — проверьте, что скопировали из того же приложения."
    try:
        from actions import spotify_premium
        spotify_premium._auth = None                      # новые ключи — новый вход
        if spotify_premium.ready():
            return "ok", "Ключи работают, вход в аккаунт выполнен."
    except Exception as exc:
        logger.debug("Spotify: %s", exc)
    return "warn", "Ключи работают. Осталось войти в аккаунт — кнопка «Войти в Spotify»."


def check_telegram(v: dict) -> tuple[str, str]:
    token = v.get("telegram_bot_token", "").strip()
    if not re.fullmatch(r"\d{6,12}:[\w-]{30,}", token):
        return "bad", "Токен бота выглядит так: 123456789:AAH… — возьмите его у @BotFather."
    users = v.get("telegram_allowed_users", "")
    users = ",".join(users) if isinstance(users, list) else str(users)
    if users.strip() and not re.fullmatch(r"[\d,\s]+", users):
        return "bad", "Telegram ID — только цифры (узнать: @userinfobot), несколько — через запятую."
    try:
        code, body = _http(f"https://api.telegram.org/bot{token}/getMe")
    except Exception as exc:
        return _net_error(exc)
    if code != 200:
        return "bad", "Токен неверный или бот удалён — создайте новый токен у @BotFather."
    name = json.loads(body).get("result", {}).get("username", "")
    if not users.strip():
        return "warn", f"Бот @{name} работает. Впишите свой Telegram ID — иначе он не знает хозяина."
    return "ok", f"Бот @{name} работает."


def check_caller(v: dict) -> tuple[str, str]:
    api_id, api_hash = str(v.get("telethon_api_id", "")).strip(), v.get("telethon_api_hash", "").strip()
    if not api_id.isdigit():
        return "bad", "API ID — число (my.telegram.org → API development tools)."
    if not re.fullmatch(r"[0-9a-f]{32}", api_hash):
        return "bad", "API Hash — 32 символа (цифры и a–f)."
    try:
        from core import tg_call
        problem = tg_call.ready()
    except Exception as exc:
        problem = str(exc)
    if not problem:
        return "ok", "Аккаунт для звонков подключён."
    return "warn", "Ключи в порядке. Осталось войти в аккаунт Джарвиса — кнопка «Войти по QR»."


def check_pc_link(v: dict) -> tuple[str, str]:
    url, token = v.get("pc_link_url", "").strip(), v.get("pc_link_token", "").strip()
    if not re.match(r"^(wss?|https?)://[\w.-]+", url):
        return "bad", "Адрес сервера бота — вида https://имя.hf.space или wss://…"
    if len(token) < 8:
        return "bad", "Секрет связи — любая длинная строка, та же, что на сервере бота (PC_LINK_TOKEN)."
    base = re.sub(r"^ws", "http", url).rstrip("/")
    try:
        code, _ = _http(base + "/", method="GET")
    except Exception as exc:
        return _net_error(exc)
    if code >= 500:
        return "warn", f"Сервер бота отвечает ошибкой {code} — возможно, спит или перезапускается."
    return "ok", "Сервер бота на связи."


def check_groq(v: dict) -> tuple[str, str]:
    key = clean_key(v.get("groq_api_key", ""))
    if not key.startswith("gsk_"):
        return "bad", "Ключ Groq начинается с «gsk_»."
    try:
        code, body = _http("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})
    except Exception as exc:
        return _net_error(exc)
    if code == 200:
        return "ok", "Ключ работает."
    if code == 401 or "invalid_api_key" in body:
        return "bad", "Ключ неверный — Groq его не принимает."
    if code == 429:
        return "warn", "Ключ верный, но лимит запросов Groq на сейчас исчерпан."
    # 403 без ответа Groq — это защита сайта (Cloudflare, VPN, страна), а не ключ
    return "warn", f"Groq не дал проверить ключ (ошибка {code}) — ключ сохранён, бот попробует его сам."


# ── сервисы ──────────────────────────────────────────────────────────────────

SERVICES: list[Service] = [
    Service("gemini", "Gemini — мозг Джарвиса", "spark",
            "Голос, разговор и все команды. Без него Джарвис не работает.",
            [Field("gemini_api_key", "API-ключ", placeholder="AIza…", env="GEMINI_API_KEY")],
            ["Откройте Google AI Studio и войдите в Google-аккаунт.",
             "Нажмите «Create API key» и скопируйте ключ целиком.",
             "Вставьте его сюда и нажмите «Сохранить и проверить»."],
            "https://aistudio.google.com/app/apikey", required=True, check=check_gemini),
    Service("fish", "Fish Audio — голос из фильма", "speak",
            "Настоящий голос Джарвиса. Без ключа он говорит стандартным голосом.",
            [Field("fish_api_key", "API-ключ", env="FISH_API_KEY"),
             Field("fish_voice_id", "ID голоса", secret=False, optional=True,
                   placeholder="по умолчанию — русский Джарвис", env="FISH_VOICE_ID")],
            ["Зарегистрируйтесь на fish.audio.",
             "Откройте «API Keys» и создайте ключ.",
             "ID голоса можно не трогать — стоит выбранный вами русский Джарвис."],
            "https://fish.audio/app/api-keys/", check=check_fish),
    Service("spotify", "Spotify — музыка", "note",
            "Включать треки, плейлисты, громкость музыки. Нужен Spotify Premium.",
            [Field("spotify_client_id", "Client ID", secret=False, placeholder="32 символа"),
             Field("spotify_client_secret", "Client Secret"),
             Field("spotify_redirect_uri", "Redirect URI", secret=False, optional=True,
                   placeholder="http://127.0.0.1:8888/callback")],
            ["Откройте developer.spotify.com → Dashboard → Create app.",
             "В Redirect URI впишите http://127.0.0.1:8888/callback, отметьте Web API.",
             "Скопируйте Client ID и Client Secret сюда, сохраните и нажмите «Войти в Spotify»."],
            "https://developer.spotify.com/dashboard", check=check_spotify, action="spotify"),
    Service("telegram", "Telegram-бот", "plane",
            "Джарвис в телефоне: напоминания, мини-приложение, управление ПК из Telegram.",
            [Field("telegram_bot_token", "Токен бота", placeholder="123456789:AAH…", env="TELEGRAM_BOT_TOKEN"),
             Field("telegram_allowed_users", "Ваш Telegram ID", secret=False, placeholder="например 123456789"),
             Field("miniapp_url", "Адрес мини-приложения", secret=False, optional=True,
                   placeholder="https://имя.hf.space")],
            ["Напишите @BotFather → /newbot, придумайте имя — он пришлёт токен.",
             "Свой Telegram ID узнайте у @userinfobot.",
             "Адрес мини-приложения — адрес вашего сервера бота (Hugging Face)."],
            "https://t.me/BotFather", check=check_telegram),
    Service("caller", "Звонки в Telegram", "phone",
            "Джарвис звонит вам голосом: будит, напоминает, говорит «пора спать».",
            [Field("telethon_api_id", "API ID", secret=False, placeholder="число", env="TELETHON_API_ID"),
             Field("telethon_api_hash", "API Hash", env="TELETHON_API_HASH")],
            ["Войдите на my.telegram.org под ОТДЕЛЬНЫМ аккаунтом Джарвиса (не вашим).",
             "API development tools → создайте приложение → скопируйте API ID и API Hash.",
             "Сохраните и нажмите «Войти по QR» — отсканируйте код с телефона Джарвиса."],
            "https://my.telegram.org/apps", check=check_caller, action="caller"),
    Service("pc_link", "Связь бота с этим ПК", "link",
            "Чтобы из Telegram можно было управлять компьютером: «выключи ПК», «что на экране».",
            [Field("pc_link_url", "Адрес сервера бота", secret=False, placeholder="https://имя.hf.space",
                   env="PC_LINK_URL"),
             Field("pc_link_token", "Секрет связи", env="PC_LINK_TOKEN")],
            ["Адрес — там же, где работает бот (Hugging Face Space).",
             "Секрет — придумайте длинную строку и впишите её и сюда, и в секреты сервера (PC_LINK_TOKEN)."],
            "", check=check_pc_link),
    Service("groq", "Groq — запасной мозг бота", "bolt",
            "Если у Gemini кончилась квота, бот в Telegram отвечает через Groq. Необязательно.",
            [Field("groq_api_key", "API-ключ", placeholder="gsk_…", env="GROQ_API_KEY")],
            ["Зарегистрируйтесь на console.groq.com.", "API Keys → Create API Key → скопируйте сюда."],
            "https://console.groq.com/keys", check=check_groq),
]
BY_ID = {s.id: s for s in SERVICES}


# ── значения ─────────────────────────────────────────────────────────────────

def load_values() -> dict:
    from core.paths import load_api_keys
    raw = load_api_keys()
    out = {}
    for s in SERVICES:
        for f in s.fields:
            v = raw.get(f.key, "")
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            v = str(v or (os.getenv(f.env, "") if f.env else ""))
            out[f.key] = clean_key(v) if f.secret and f.key not in _AS_TYPED else v.strip()
    return out


def from_env(f: Field) -> bool:
    """Значение пришло из переменной среды — окно его не перезапишет."""
    return bool(f.env and os.getenv(f.env))


def save_values(changes: dict) -> bool:
    from core.paths import save_api_keys
    secret = {f.key for sv in SERVICES for f in sv.fields if f.secret} - _AS_TYPED
    clean = {}
    for k, v in changes.items():
        v = clean_key(v) if k in secret else str(v or "").strip()
        if k == "telegram_allowed_users":
            clean[k] = [int(x) for x in re.findall(r"\d+", v)]
        elif k == "telethon_api_id" and v.isdigit():
            clean[k] = int(v)
        else:
            clean[k] = v
    ok = save_api_keys(clean)
    logger.info("Ключи сохранены: %s", ", ".join(f"{k}={mask(str(v))}" for k, v in clean.items()))
    return ok


def mask(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def status(s: Service, values: dict) -> str:
    """Без сети: "missing" — не задан, "partial" — не всё обязательное, "set" — всё на месте."""
    need = [f for f in s.fields if not f.optional]
    have = [f for f in need if values.get(f.key)]
    if not have:
        return "missing"
    return "set" if len(have) == len(need) else "partial"


def check(service_id: str, values: dict) -> tuple[str, str]:
    s = BY_ID[service_id]
    st = status(s, values)
    if st == "missing":
        return "missing", "Не задан." if not s.required else "Нужен обязательно — без него Джарвис не работает."
    if st == "partial":
        miss = [f.label for f in s.fields if not f.optional and not values.get(f.key)]
        return "bad", "Не хватает: " + ", ".join(miss) + "."
    try:
        return s.check(values) if s.check else ("ok", "Задан.")
    except Exception as exc:
        logger.warning("Проверка %s: %s", service_id, exc)
        return "warn", f"Проверка не удалась: {type(exc).__name__}."


def check_all(values: dict | None = None) -> dict[str, tuple[str, str]]:
    values = values if values is not None else load_values()
    return {s.id: check(s.id, values) for s in SERVICES if status(s, values) != "missing" or s.required}
