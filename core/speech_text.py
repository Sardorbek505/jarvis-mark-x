"""Текст для голоса и для чата — без кодов, ссылок и технических имён.

Владелец: «не надо мне читать какие-то коды и показывать их в чате». Модель,
инструменты и исключения порождают ссылки, пути, имена функций (web_search),
тексты ошибок (ConnectionResetError: …), служебные метки (<ctrl46>, <noise>),
markdown. В озвучку это шло как есть — Джарвис читал «эйч ти ти пи эс…».

for_speech — то, что можно произнести: ссылка → «ссылка», путь → «файл»,
код и технические id выброшены. for_chat — то, что показать: без разметки и
блоков кода, но ссылки остаются (по ним можно нажать).
"""
from __future__ import annotations

import re

_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.S)
_INLINE = re.compile(r"`([^`]*)`")
_MD = re.compile(r"(\*\*|__|~~|^#{1,6}\s+|^>\s?)", re.M)
_URL = re.compile(r"(?:https?://|www\.)\S+?(?=[,.!?;:)»\]]*(?:\s|$))", re.I)
# C:\Users\…\file.ext, /home/…/file.py, ..\a\b.json — путь с хотя бы одним разделителем.
_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])?(?:[\w.\-]+[\\/])+[\w.\-]+\.\w{1,5}\b")
_TOOL_ID = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")          # web_search, open_app, pc_link_token
_EXC = re.compile(r"\b[A-Z]\w*(?:Error|Exception)\b:?[^.!?\n]*")
_TAGS = re.compile(r"<\s*ctrl\d+\s*>|<\s*/?\s*(?:noise|laughter|applause|whisper|gasp|groan)\s*>|\[\[.*?\]\]",
                   re.I)
_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{16,}\b", re.I)
_JSON = re.compile(r"\{[^{}]*\"[^{}]*\":[^{}]*\}")
_SPACE = re.compile(r"[ \t]+")
_SPACE_PUNCT = re.compile(r"\s+([,.!?:;])")


def for_chat(text: str) -> str:
    t = _CODE_BLOCK.sub(" ", text or "")
    t = _INLINE.sub(r"\1", t)
    t = _TAGS.sub("", t)
    t = _MD.sub("", t)
    return _SPACE.sub(" ", t).strip()


def for_speech(text: str) -> str:
    """Произносимый текст. Пробелы по краям сохраняются: озвучка идёт кусками,
    и «Привет,» + « сэр» не должны слипнуться."""
    if not text:
        return ""
    lead = " " if text[:1].isspace() else ""
    trail = " " if text[-1:].isspace() else ""
    t = _CODE_BLOCK.sub(" ", text)
    t = _INLINE.sub(r"\1", t)
    t = _TAGS.sub("", t)
    t = _JSON.sub("", t)
    t = _URL.sub("ссылка", t)
    t = _PATH.sub("файл", t)
    t = _EXC.sub("", t)
    t = _TOOL_ID.sub("", t)
    t = _HEX.sub("", t)
    t = _MD.sub("", t)
    t = t.replace("*", "").replace("#", "").replace("_", " ")
    t = _SPACE_PUNCT.sub(r"\1", _SPACE.sub(" ", t)).strip()
    t = re.sub(r"([,:;])\s*([.!?])", r"\2", t)                # «Ошибка: .» после вырезания
    t = re.sub(r"\b(файл|ссылк[аиу])\s+\1\b", r"\1", t, flags=re.I)   # «файл файл»
    t = t.rstrip(":;— ").strip()                # запятую не трогаем: по ней режется озвучка
    return (lead + t + trail) if t else ""


def short_reason(exc: BaseException | str) -> str:
    """Причина сбоя по-человечески — вместо текста исключения."""
    s = str(exc).lower()
    if any(k in s for k in ("timed out", "timeout", "таймаут")):
        return "не ответило вовремя"
    if any(k in s for k in ("connection", "network", "getaddrinfo", "ssl", "сеть", "unreachable")):
        return "нет связи"
    if any(k in s for k in ("permission", "access is denied", "доступ")):
        return "нет доступа"
    if any(k in s for k in ("not found", "no such file", "не найден")):
        return "не нашёл нужное"
    if any(k in s for k in ("429", "quota", "rate limit", "resource_exhausted")):
        return "слишком много запросов, нужна пауза"
    return "что-то пошло не так"
