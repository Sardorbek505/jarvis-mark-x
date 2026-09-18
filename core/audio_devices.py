"""
Выбор микрофона и динамиков — по имени, а не наугад.

ЧТО БЫЛО НЕ ТАК
    Микрофон выбирала эвристика по подстрокам в названии: сначала «headset»,
    «bluetooth», «наушник», потом «noise-cancelling», потом «realtek» и
    «массив». Работает, пока угадывает. Когда не угадала — а на чужой машине
    названия другие, — починить это пользователь не может никак: ни списка, ни
    выбора, ни даже способа узнать, какое устройство взято. «Джарвис меня не
    слышит» почти всегда означает «Джарвис слушает веб-камеру».

    Устройство вывода не выбиралось вовсе: `device=None`, то есть «то, что
    система назвала по умолчанию», а в Windows это меняется само при
    подключении гарнитуры.

ПОЧЕМУ СПИСОК КОРОТКИЙ
    `query_devices()` отдаёт по записи на КАЖДУЮ пару устройство × host API, а
    не на устройство: один и тот же микрофон появляется под MME, DirectSound,
    WASAPI и WDM-KS. Это не выбор, а викторина. Здесь остаётся один host API на
    направление, отбрасываются псевдоустройства вроде «Sound Mapper» (они
    означают всего лишь «по умолчанию») и снимаются дубли по имени.

ПОЧЕМУ ПО ИМЕНИ, А НЕ ПО ИНДЕКСУ
    Индексы сдвигаются при каждом подключении наушников. Сохранённый индекс
    назавтра указывает на другое устройство — молча и без единого признака.
    Имя переживает переключения, а если устройство исчезло, мы честно
    откатываемся на системное по умолчанию и пишем об этом в лог.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("jarvis.audio")

# «Устройства», которые на самом деле значат «возьми то, что по умолчанию».
# Выбирать их из списка бессмысленно: это и есть поведение без выбора.
_ПСЕВДО = (
    "sound mapper", "primary sound", "основной звуковой",
    "звуковое сопоставление", "default", "по умолчанию",
    "@system32", "sysdefault", "pulse", "pipewire",
)

# Порядок предпочтения host API. Первый найденный и берётся: смешивать их в
# одном списке нельзя — получится тот самый список из сорока строк.
_ПРЕДПОЧТЕНИЕ_API = ("mme", "windows directsound", "core audio", "alsa", "asound")


def _псевдо(имя: str) -> bool:
    низ = имя.lower()
    return any(к in низ for к in _ПСЕВДО)


def _api_имя(индекс_api: int, api_список) -> str:
    try:
        return str(api_список[индекс_api]["name"]).lower()
    except Exception:
        return ""


def list_devices(kind: str = "input") -> list[dict]:
    """Короткий список устройств: имя, индекс, число каналов.

    `kind` — "input" или "output". Пустой список означает, что звуковой
    подсистемы нет вовсе (так бывает в контейнерах и на сервере)."""
    try:
        import sounddevice as sd
        устройства = sd.query_devices()
        апи = sd.query_hostapis()
    except Exception as exc:
        logger.warning("Список устройств недоступен: %s", exc)
        return []

    поле = "max_input_channels" if kind == "input" else "max_output_channels"

    подходящие = []
    for индекс, d in enumerate(устройства):
        if d.get(поле, 0) < 1:
            continue
        имя = str(d.get("name", "")).strip()
        if not имя or _псевдо(имя):
            continue
        подходящие.append({
            "index": индекс,
            "name": имя,
            "channels": d.get(поле, 0),
            "api": _api_имя(d.get("hostapi", 0), апи),
        })

    if not подходящие:
        return []

    # Один host API на направление — тот, что раньше в списке предпочтений и
    # реально присутствует на этой машине.
    доступные_api = {d["api"] for d in подходящие}
    выбранный = next((a for a in _ПРЕДПОЧТЕНИЕ_API if a in доступные_api), None)
    if выбранный is not None:
        подходящие = [d for d in подходящие if d["api"] == выбранный]

    # Дубли по имени: одно устройство может встретиться дважды даже внутри
    # одного API.
    видели: set[str] = set()
    итог = []
    for d in подходящие:
        ключ = d["name"].lower()
        if ключ in видели:
            continue
        видели.add(ключ)
        итог.append(d)
    return итог


def resolve(имя: str, kind: str = "input") -> int | None:
    """Индекс устройства по сохранённому имени.

    None — «берите системное по умолчанию»: либо имя не задано, либо
    устройство отключили. Второй случай пишется в лог: молчаливый откат на
    другое устройство неотличим от поломки."""
    имя = (имя or "").strip()
    if not имя:
        return None

    устройства = list_devices(kind)
    низ = имя.lower()

    for d in устройства:
        if d["name"].lower() == низ:
            return d["index"]
    # Названия меняются в мелочах при обновлении драйвера («(2- Realtek…)»),
    # поэтому вторым заходом ищем вхождение.
    for d in устройства:
        if низ in d["name"].lower() or d["name"].lower() in низ:
            return d["index"]

    logger.warning("Устройство «%s» не найдено — беру системное по умолчанию", имя)
    return None


# ─── Сохранённый выбор ────────────────────────────────────────────────────────

_КЛЮЧ = {"input": "input_device", "output": "output_device"}


def saved_name(kind: str = "input") -> str:
    try:
        from core.paths import load_api_keys
        return str(load_api_keys().get(_КЛЮЧ[kind], "") or "").strip()
    except Exception as exc:
        logger.debug("Настройки устройств не прочитались: %s", exc)
        return ""


def save_name(имя: str, kind: str = "input") -> bool:
    """Сохраняет выбор. Пустое имя означает «вернуться к системному»."""
    try:
        from core.paths import load_api_keys, save_api_keys
        данные = load_api_keys()
        данные[_КЛЮЧ[kind]] = (имя or "").strip()
        save_api_keys(данные)
        return True
    except Exception as exc:
        logger.warning("Выбор устройства не сохранён: %s", exc)
        return False


def chosen_index(kind: str = "input") -> int | None:
    """Индекс устройства, выбранного человеком, либо None."""
    return resolve(saved_name(kind), kind)
