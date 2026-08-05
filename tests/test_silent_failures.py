"""Сломанная память не должна выглядеть как «Джарвис тебя забыл».

Класс дефектов, найденный аудитом: обработчик ловит исключение, подставляет
пустое значение и молчит. Ответ приходит уверенный — просто без фактов, без
имени, без напоминаний. Снаружи это неотличимо от «модель тупит».
"""
import asyncio

import pytest

from telegram_bot import onboarding, reminders


class _BrokenMemory:
    async def get_meta(self, uid, key):
        raise RuntimeError("база недоступна")


class _NewUserMemory:
    async def get_meta(self, uid, key):
        return None          # такого ключа нет — это НОВЫЙ человек, не сбой


@pytest.mark.asyncio
async def test_broken_memory_does_not_turn_a_known_user_into_a_stranger(caplog):
    # Arrange / Act
    caplog.set_level("WARNING")
    result = await onboarding.already_onboarded(_BrokenMemory(), 1)

    # Assert — при сбое считаем знакомым, иначе бот заново спросит имя
    assert result is True
    assert any("онбординг" in r.message.lower() or "онбординг" in str(r.msg).lower()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_genuinely_new_user_still_goes_through_onboarding():
    # Отсутствие ключа — это не сбой, и подменять его «знакомым» нельзя
    assert await onboarding.already_onboarded(_NewUserMemory(), 1) is False


def test_unparsable_reminder_time_is_reported(caplog):
    # Arrange / Act — на входе мусор вместо ISO
    caplog.set_level("WARNING")
    out = reminders.fmt_local("не время вовсе", timezone_stub := None)

    # Assert — пользователь увидит исходную строку, но сбой попадёт в лог
    assert out == "не время вовсе"
    assert caplog.records


def test_valid_reminder_time_is_converted():
    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=5))
    assert reminders.fmt_local("2026-08-05T14:30:00", tz) == "05.08 в 19:30"
