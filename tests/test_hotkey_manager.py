"""Глобальные горячие клавиши есть только на Windows — и молчат везде ещё.

`GlobalHotkeyManager` работает через Win32 `RegisterHotKey`, и `start()` на
других системах сознательно ничего не делает: аналога у macOS и Linux нет, а
падать при запуске из-за отсутствующей возможности ассистент не должен.

Прежний тест проверял только вторую половину — что поток поднялся, — и потому
падал на каждой не-Windows машине. Красный тест в каждом прогоне приучает не
смотреть на красное, то есть обходится дороже, чем непроверенная ветка.
Теперь проверяются обе половины, каждая на своей системе.
"""

import sys
import time

import pytest

from core.hotkey_manager import GlobalHotkeyManager


@pytest.fixture
def менеджер():
    нажатия = []
    m = GlobalHotkeyManager(on_wake=lambda: нажатия.append("wake"),
                            on_mute=lambda: нажатия.append("mute"))
    m.нажатия = нажатия
    try:
        yield m
    finally:
        m.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="ветка для не-Windows")
def test_вне_windows_запуск_молча_ничего_не_делает(менеджер):
    """Не «падает с ошибкой» и не «делает вид, что работает» — просто не
    поднимает поток. Ассистент при этом остаётся живым."""
    менеджер.start()

    assert менеджер._thread is None
    assert менеджер.нажатия == []


@pytest.mark.skipif(sys.platform == "win32", reason="ветка для не-Windows")
def test_вне_windows_останов_без_запуска_безопасен(менеджер):
    """`stop()` зовётся из `cleanup()` всегда, в том числе когда `start()`
    ничего не поднял. Исключение здесь ломало бы выход из программы."""
    менеджер.stop()          # не должно поднять исключение
    менеджер.start()
    менеджер.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey есть только на Windows")
def test_на_windows_поток_живёт_и_останавливается(менеджер):
    менеджер.start()
    assert менеджер._thread is not None
    assert менеджер._thread.is_alive()

    time.sleep(0.1)
    менеджер.stop()
    time.sleep(0.3)

    assert not менеджер._thread.is_alive(), "поток не завершился после stop()"


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey есть только на Windows")
def test_повторный_запуск_не_плодит_потоков(менеджер):
    """Иначе каждое переподключение добавляло бы ещё один слушатель, и одно
    нажатие F8 будило бы Джарвиса трижды."""
    менеджер.start()
    первый = менеджер._thread
    менеджер.start()

    assert менеджер._thread is первый
