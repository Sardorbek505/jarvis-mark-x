"""
Один вход к языковой модели для текстовых задач.

ЧТО БЫЛО НЕ ТАК
    Пересказ файла, пересказ ролика и поиск — три места, и в каждом свой
    экземпляр клиента Gemini, своё получение ключа, свой таймаут и своя
    обработка ошибки. Добавить четвёртое место значило скопировать этот блок
    ещё раз, а сменить модель — найти все три.

    Хуже другое: когда у Gemini кончается квота, все три замолкают
    одновременно, и человек слышит «пересказать не смог» на каждый вопрос —
    при том, что на его же машине может стоять локальная модель, которой
    такая задача вполне по силам.

ЧТО ЭТО И ЧТО НЕ ЭТО
    Это слой для ТЕКСТОВЫХ задач: дать текст — получить текст. Пересказ,
    выжимка, объяснение, резюме разговора.

    Это НЕ замена Gemini Live. Голосовой ход — слух, речь и определение конца
    фразы — живёт внутри Live API, и вынести его сюда нельзя: понадобился бы
    второй турн-цикл с собственным VAD (см. tasks/provider-layer.md, где это
    расписано как двухнедельная работа). Обещать здесь «оффлайн-режим» было
    бы враньём; честное описание — «текстовые задачи переживут кончившуюся
    квоту».

ПОРЯДОК ПРОВАЙДЕРОВ
    Сначала выбранный (настройка `llm_provider` или JARVIS_LLM), затем
    остальные доступные. Провайдер считается доступным, только если у него
    есть чем работать: у Gemini — ключ, у Ollama — отвечающий сервер, у
    OpenAI-совместимого — адрес и ключ. Живость Ollama кэшируется на минуту:
    иначе каждый пересказ платил бы таймаутом соединения за выяснение того,
    что уже выяснено.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

_logger = logging.getLogger(__name__)

ПРОВАЙДЕРЫ = ("gemini", "ollama", "openai")

_ТАЙМАУТ_ПО_УМОЛЧАНИЮ = 45
_ПРОБА_OLLAMA_СЕК = 3
_ЖИВОСТЬ_ГОДНА_СЕК = 60

_OLLAMA_ПО_УМОЛЧАНИЮ = "http://localhost:11434"
_МОДЕЛЬ_OLLAMA = "llama3.1"
_МОДЕЛЬ_GEMINI = "gemini-2.5-flash"
_МОДЕЛЬ_OPENAI = "gpt-4o-mini"

# Когда в последний раз выяснили, жив ли Ollama, и что выяснили.
_живость: dict[str, tuple[float, bool]] = {}


# ─── Настройки ───────────────────────────────────────────────────────────────

def _настройки() -> dict:
    try:
        from core.paths import load_api_keys
        данные = load_api_keys()
        return данные if isinstance(данные, dict) else {}
    except Exception as exc:
        _logger.debug("Настройки не прочитаны: %s", exc)
        return {}


def _значение(ключ_настройки: str, переменная: str, по_умолчанию: str = "") -> str:
    из_среды = os.getenv(переменная, "").strip()
    if из_среды:
        return из_среды
    return str(_настройки().get(ключ_настройки, "") or "").strip() or по_умолчанию


def выбранный() -> str:
    """Какой провайдер человек назначил главным. Пусто — решаем сами."""
    имя = _значение("llm_provider", "JARVIS_LLM").lower()
    return имя if имя in ПРОВАЙДЕРЫ else ""


# ─── Доступность ─────────────────────────────────────────────────────────────

def _ключ_gemini() -> str:
    try:
        from core.onboarding import ensure_gemini_key
        return (ensure_gemini_key(interactive=False) or "").strip()
    except Exception as exc:
        _logger.debug("Ключ Gemini недоступен: %s", exc)
        return ""


def _адрес_ollama() -> str:
    return _значение("ollama_url", "OLLAMA_URL", _OLLAMA_ПО_УМОЛЧАНИЮ).rstrip("/")


def _ollama_жив(адрес: str) -> bool:
    сейчас = time.time()
    было = _живость.get(адрес)
    if было and сейчас - было[0] < _ЖИВОСТЬ_ГОДНА_СЕК:
        return было[1]

    жив = False
    try:
        with urllib.request.urlopen(f"{адрес}/api/tags",
                                    timeout=_ПРОБА_OLLAMA_СЕК) as ответ:
            жив = ответ.status == 200
    except Exception as exc:
        _logger.debug("Ollama на %s не отвечает: %s", адрес, exc)

    _живость[адрес] = (сейчас, жив)
    return жив


def доступен(провайдер: str) -> bool:
    if провайдер == "gemini":
        return bool(_ключ_gemini())
    if провайдер == "ollama":
        return _ollama_жив(_адрес_ollama())
    if провайдер == "openai":
        return bool(_значение("openai_key", "OPENAI_API_KEY"))
    return False


def _порядок(кроме: tuple[str, ...] = ()) -> list[str]:
    главный = выбранный()
    очередь = ([главный] if главный else []) + [п for п in ПРОВАЙДЕРЫ if п != главный]
    return [п for п in очередь if п not in кроме and доступен(п)]


def available() -> bool:
    return bool(_порядок())


def describe() -> str:
    """Чем именно умеет отвечать эта машина — словами, для лога и ответа."""
    живые = _порядок()
    if not живые:
        return "Текстовой модели сейчас нет: ни ключа Gemini, ни локального Ollama."
    имена = {"gemini": "Gemini", "ollama": f"Ollama ({_модель_ollama()})",
             "openai": "OpenAI-совместимый"}
    return "Отвечаю через " + ", затем ".join(имена[п] for п in живые) + "."


def _модель_ollama() -> str:
    return _значение("ollama_model", "OLLAMA_MODEL", _МОДЕЛЬ_OLLAMA)


# ─── Провайдеры ──────────────────────────────────────────────────────────────

def _спросить_gemini(запрос: str, система: str, предел: int,
                     температура: float, таймаут: int) -> str:
    # `таймаут` здесь не используется: клиент google-genai держит свой и
    # менять его на вызов не даёт. Подпись общая, чтобы вызывающий не знал,
    # какой из провайдеров чем отличается.
    from google import genai
    from google.genai import types

    клиент = genai.Client(api_key=_ключ_gemini())
    ответ = клиент.models.generate_content(
        model=_значение("llm_model", "JARVIS_LLM_MODEL", _МОДЕЛЬ_GEMINI),
        contents=запрос,
        config=types.GenerateContentConfig(
            system_instruction=система or None,
            temperature=температура,
            max_output_tokens=предел,
        ),
    )
    return (ответ.text or "").strip()


def _почтой(url: str, тело: dict, таймаут: int, заголовки: dict | None = None) -> dict:
    данные = json.dumps(тело).encode("utf-8")
    запрос = urllib.request.Request(
        url, data=данные,
        headers={"Content-Type": "application/json", **(заголовки or {})})
    with urllib.request.urlopen(запрос, timeout=таймаут) as ответ:
        return json.loads(ответ.read().decode("utf-8"))


def _спросить_ollama(запрос: str, система: str, предел: int,
                     температура: float, таймаут: int) -> str:
    сообщения = ([{"role": "system", "content": система}] if система else [])
    сообщения.append({"role": "user", "content": запрос})
    ответ = _почтой(
        f"{_адрес_ollama()}/api/chat",
        {
            "model": _модель_ollama(),
            "messages": сообщения,
            "stream": False,
            "options": {"temperature": температура, "num_predict": предел},
        },
        таймаут,
    )
    return str((ответ.get("message") or {}).get("content", "")).strip()


def _спросить_openai(запрос: str, система: str, предел: int,
                     температура: float, таймаут: int) -> str:
    основа = _значение("openai_base", "OPENAI_BASE_URL",
                       "https://api.openai.com/v1").rstrip("/")
    сообщения = ([{"role": "system", "content": система}] if система else [])
    сообщения.append({"role": "user", "content": запрос})
    ответ = _почтой(
        f"{основа}/chat/completions",
        {
            "model": _значение("openai_model", "OPENAI_MODEL", _МОДЕЛЬ_OPENAI),
            "messages": сообщения,
            "temperature": температура,
            "max_tokens": предел,
        },
        таймаут,
        {"Authorization": f"Bearer {_значение('openai_key', 'OPENAI_API_KEY')}"},
    )
    выборы = ответ.get("choices") or []
    if not выборы:
        return ""
    return str((выборы[0].get("message") or {}).get("content", "")).strip()


_СПРОСИТЬ = {
    "gemini": _спросить_gemini,
    "ollama": _спросить_ollama,
    "openai": _спросить_openai,
}


# ─── Общий вход ──────────────────────────────────────────────────────────────

def ask(запрос: str, *, система: str = "", предел: int = 700,
        температура: float = 0.3, таймаут: int = _ТАЙМАУТ_ПО_УМОЛЧАНИЮ,
        кроме: tuple[str, ...] = ()) -> str:
    """Текст на вход — текст на выход. Пустая строка означает «не вышло».

    `кроме` — провайдеры, которых пропустить. Нужно тому, кто только что
    попробовал одного сам и получил отказ: повторная попытка стоит ещё
    одного таймаута на пути, который и так уже деградировал.

    Вызывающий обязан сказать об этом вслух: молчаливый пустой ответ
    выглядит как «Джарвис меня не понял»."""
    if not (запрос or "").strip():
        return ""

    for провайдер in _порядок(кроме):
        try:
            ответ = _СПРОСИТЬ[провайдер](запрос, система, предел, температура, таймаут)
        except Exception as exc:
            # Ключ кончился, квота исчерпана, сервер лёг — всё это причины
            # попробовать следующего, а не молчать.
            _logger.warning("%s не ответил: %s", провайдер, exc)
            if провайдер == "ollama":
                _живость.pop(_адрес_ollama(), None)
            continue
        if ответ:
            _logger.info("Текстовый ответ от %s (%d символов)", провайдер, len(ответ))
            return ответ
        _logger.warning("%s ответил пустотой", провайдер)

    return ""


def reset() -> None:
    """Забыть, кто был жив. Нужен тестам и смене настроек."""
    _живость.clear()
