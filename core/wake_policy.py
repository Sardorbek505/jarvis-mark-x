"""Когда Джарвис слушает без слова «Джарвис» — и слушает ли вообще.

Строгий режим (по умолчанию, `JARVIS_STRICT_WAKE=1`) — как Алиса и Siri:
имя обязательно для каждой фразы. Исключения ровно два: Джарвис сам задал
вопрос или попросил уточнить — тогда ответ ждётся без имени.

Мягкий режим (`JARVIS_STRICT_WAKE=0`) оставляет короткое окно после любого
ответа («а в Москве?»), ценой ложных подхватов чужой речи рядом.

До этого без имени микрофон открывался пятью способами: окно после каждого
ответа, перебивание по громкости (телевизор → 8 с открытого микрофона),
15 с после инструмента, промежуточные результаты Vosk и приём фразы без
имени в расшифровке, если детектор «что-то услышал».
"""

from __future__ import annotations

import os

# Столько секунд между срабатыванием слова и первой расшифровкой фразы ещё
# считается «одной репликой»: имя съедает локальный детектор, в тексте от
# Gemini его может не быть («, какая погода в Шымкенте»), а сама расшифровка
# приходит через 3–5 с после слова — после конца фразы. Запас как у окна
# после слова; ложное срабатывание без речи снимает таймер тишины
# (main._on_listen_silence_timeout сбрасывает addressed).
WAKE_TO_SPEECH_MAX_GAP_SEC = 8.0

FOLLOW_UP_AFTER_QUESTION_SEC = 6.0
FOLLOW_UP_AFTER_ANSWER_SOFT_SEC = 3.5
FOLLOW_UP_AFTER_APOLOGY_SEC = 2.0


def is_strict() -> bool:
    return os.getenv("JARVIS_STRICT_WAKE", "1").strip() not in ("0", "false", "no", "off")


def follow_up_window(asked_question: bool, is_apology: bool = False) -> float:
    """Сколько секунд слушать без имени после ответа. 0 — сразу в ожидание."""
    if asked_question:
        return FOLLOW_UP_AFTER_QUESTION_SEC
    if is_apology:
        # «Не разобрал, повторить?» — Джарвис сам просит повторить, имя не нужно
        return FOLLOW_UP_AFTER_APOLOGY_SEC
    if is_strict():
        return 0.0
    return FOLLOW_UP_AFTER_ANSWER_SOFT_SEC


def rms_barge_in_enabled() -> bool:
    """Перебивать по громкости (а не только словом) — лишь в мягком режиме."""
    return not is_strict()


def gate_after_interrupt(reason: str) -> float:
    """Сколько держать микрофон открытым после перебивания.

    Перебили словом «Джарвис» — фраза уже началась, ждём её. Перебили
    громкостью — в строгом режиме это не обращение, ничего не открываем.
    """
    if "wake" in (reason or "").lower() or "kws" in (reason or "").lower():
        return 8.0
    return 0.0 if is_strict() else 8.0


def is_addressed(
    name_in_text: bool,
    wake_spotted: bool,
    wake_to_speech_gap: float | None,
    hotkey: bool = False,
    gate_open: bool = False,
    active_dialog: bool = False,
) -> bool:
    """Обращена ли фраза к Джарвису.

    Строгий режим: имя в тексте, либо слово сработало и речь началась сразу
    за ним, либо хоткей, либо Джарвис сам ждёт ответа (active_dialog).
    Открытый шлюз сам по себе (окно после ответа) в строгом режиме ничего не
    решает — его в строгом режиме и не открывают.
    """
    if name_in_text or hotkey or active_dialog:
        return True
    if wake_spotted:
        if wake_to_speech_gap is None:
            return not is_strict()
        return wake_to_speech_gap <= WAKE_TO_SPEECH_MAX_GAP_SEC or not is_strict()
    return gate_open and not is_strict()
