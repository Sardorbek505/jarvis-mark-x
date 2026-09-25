"""Запасные модели для Telegram-бота: когда Gemini упёрся в лимит или лежит.

Мозг у Джарвиса один — память, досье, характер и история живут у нас
(memory_store, _SYSTEM_PROMPT, контекст-провайдер). Модель — только «язык»,
которым этот мозг думает сейчас. Поэтому запасная модель получает ровно тот
же системный промпт и ту же историю, что получил бы Gemini, и отвечает
тем же Джарвисом.

Каждый провайдер — отдельный сервис со своим честным бесплатным лимитом и
своим ключом. Ротация нескольких ключей ОДНОГО сервиса не подходит: у Gemini
лимит считается на проект, а плодить аккаунты ради обхода лимитов запрещено
условиями сервиса.

Все три говорят на OpenAI-совместимом /chat/completions, поэтому клиент один.
Провайдер попадает в цепочку, только если для него задан ключ — в env
(облако) или в config/api_keys.json (ПК). Модель можно сменить env-переменной
<ИМЯ>_MODEL: бесплатные линейки у провайдеров меняются чаще, чем код.
"""

import json
import logging
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
_TIMEOUT_SEC = 40.0
# Упёрся в лимит — провайдер отдыхает, чтобы не тратить на него каждый запрос.
_COOLDOWN_SEC = 60.0


class Provider(NamedTuple):
    name: str
    base_url: str
    key: str
    model: str


# Порядок — порядок в цепочке: быстрый и щедрый первым.
_KNOWN = (
    # name,        base_url,                           ключ в конфиге,        модель по умолчанию
    ("groq",       "https://api.groq.com/openai/v1",   "groq_api_key",        "llama-3.3-70b-versatile"),
    ("openrouter", "https://openrouter.ai/api/v1",     "openrouter_api_key",  "meta-llama/llama-3.3-70b-instruct:free"),
    ("mistral",    "https://api.mistral.ai/v1",        "mistral_api_key",     "mistral-small-latest"),
)


@lru_cache(maxsize=1)
def _from_config() -> dict:
    try:
        return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def providers() -> list[Provider]:
    """Провайдеры, для которых есть ключ, в порядке цепочки."""
    out = []
    for name, base_url, cfg_key, default_model in _KNOWN:
        env = name.upper()
        key = (os.getenv(f"{env}_API_KEY") or _from_config().get(cfg_key, "")).strip()
        if not key or key.startswith("your_"):
            continue
        model = (os.getenv(f"{env}_MODEL") or _from_config().get(f"{name}_model", "")
                 or default_model).strip()
        out.append(Provider(name, base_url, key, model))
    return out


def to_messages(contents, system_instruction: str = "") -> list[dict] | None:
    """История в формате Gemini → сообщения OpenAI.

    None — если в запросе есть не-текст (голос, картинка, документ): такие
    запросы запасной текстовой модели не отдаём, она их не поймёт.
    """
    if isinstance(contents, str):
        contents = [{"role": "user", "parts": [{"text": contents}]}]
    messages = [{"role": "system", "content": system_instruction}] if system_instruction else []
    for item in contents or []:
        if not isinstance(item, dict):
            return None
        texts = []
        for part in item.get("parts") or []:
            if not isinstance(part, dict) or set(part) - {"text"}:
                return None
            texts.append(part.get("text") or "")
        role = "assistant" if item.get("role") == "model" else "user"
        messages.append({"role": role, "content": "".join(texts)})
    return messages


class FallbackChain:
    """Пробует провайдеров по очереди; упёршегося в лимит откладывает."""

    def __init__(self, provider_list: list[Provider] | None = None):
        self._providers = providers() if provider_list is None else provider_list
        self._resting_until: dict[str, float] = {}
        if self._providers:
            logger.info("Запасные модели: %s",
                        ", ".join(f"{p.name}/{p.model}" for p in self._providers))

    def __bool__(self) -> bool:
        return bool(self._providers)

    async def complete(self, contents, system_instruction: str = "",
                       temperature: float = 0.7) -> str | None:
        messages = to_messages(contents, system_instruction)
        if messages is None:
            return None
        import httpx

        now = time.monotonic()
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            for p in self._providers:
                if self._resting_until.get(p.name, 0.0) > now:
                    continue
                try:
                    r = await client.post(
                        f"{p.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {p.key}"},
                        json={"model": p.model, "messages": messages,
                              "temperature": temperature},
                    )
                    if r.status_code == 429:
                        self._resting_until[p.name] = now + _COOLDOWN_SEC
                        logger.warning("Запасная модель %s: лимит, отдыхает %.0f с",
                                       p.name, _COOLDOWN_SEC)
                        continue
                    r.raise_for_status()
                    text = (r.json()["choices"][0]["message"]["content"] or "").strip()
                    if text:
                        logger.warning("Ответ дала запасная модель %s/%s", p.name, p.model)
                        return text
                except Exception as e:
                    logger.error("Запасная модель %s не ответила: %s", p.name, e)
        return None
