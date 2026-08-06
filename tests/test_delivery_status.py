"""Успех отправки определяется статусом, а не словом в тексте.

Ответ ПК содержал только фразу для человека («✅ Отправлено»), и сервер
восстанавливал по ней булево. Для очереди отложенных сообщений это опасно:
не совпало слово — запись не удаляется и уходит адресату заново при каждом
переподключении ПК.

Сервер и домашний ПК обновляются порознь, поэтому старый способ признаётся
как запасной, пока на ПК крутится прежняя сборка.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_bot.pc_bridge import PCBridge


@pytest.mark.parametrize("res", [
    {"ok": True, "text": "✅ Отправлено"},
    {"ok": True, "text": "✅ Отправлено (голосом)"},
    {"ok": True, "text": "как угодно переформулированный успех"},
])
def test_явный_статус_главнее_текста(res):
    assert PCBridge.delivered(res) is True


@pytest.mark.parametrize("res", [
    {"ok": False, "text": "❌ Не отправлено: нет такого контакта"},
    {"ok": False, "text": "✅ Отправлено"},   # текст врёт — верим статусу
])
def test_отказ_распознаётся_даже_при_бодром_тексте(res):
    assert PCBridge.delivered(res) is False


def test_старый_клиент_без_поля_ok():
    """Пока на ПК прежняя сборка, признаём и слово в тексте."""
    assert PCBridge.delivered({"text": "✅ Отправлено"}) is True
    assert PCBridge.delivered({"text": "❌ Не отправлено: таймаут"}) is False


@pytest.mark.parametrize("res", [None, {}, {"text": ""}, {"text": None}])
def test_нет_ответа_это_не_доставлено(res):
    assert PCBridge.delivered(res) is False
