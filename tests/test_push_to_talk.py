"""«Зажми и говори»: микрофон закрыт, пока клавишу не держат.

Пробуждение голосом хорошо, пока в комнате тихо. На созвоне, при фильме или
рядом с говорящими людьми микрофон открыт всегда, и каждая чужая фраза —
повод встрять.

Главное свойство режима не в удобстве, а в том, что кадры НЕ уезжают из дома,
пока клавишу не держат. Это и проверяется — вплоть до микрофонного гейта в
main.py.
"""

import sys
import time
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import push_to_talk as ptt
from core.push_to_talk import PushToTalk


@pytest.fixture
def клавиша():
    """Режим без опроса: состояние задаётся вручную, как на macOS и Linux."""
    события = []
    p = PushToTalk(on_change=события.append)
    yield p, события
    p.stop()


# ─── Состояние ────────────────────────────────────────────────────────────────

def test_по_умолчанию_микрофон_закрыт(клавиша):
    p, _ = клавиша
    assert p.held is False
    assert p.mic_open() is False, "молчаливо открытый микрофон — это не PTT"


def test_удержание_открывает_микрофон(клавиша):
    p, _ = клавиша
    p.set_held(True)

    assert p.held is True
    assert p.mic_open() is True


def test_после_отпускания_остаётся_хвост(клавиша):
    """Человек отпускает клавишу ровно на последнем звуке, а звук идёт блоками:
    без хвоста обрывается последний слог."""
    p, _ = клавиша
    p.set_held(True)
    p.set_held(False)

    assert p.held is False
    assert p.mic_open() is True, "хвост ещё не истёк"


def test_хвост_кончается(клавиша, monkeypatch):
    p, _ = клавиша
    p.set_held(True)
    p.set_held(False)

    настоящее = time.monotonic
    monkeypatch.setattr(ptt.time, "monotonic",
                        lambda: настоящее() + ptt.RELEASE_TAIL_SEC + 0.1)

    assert p.mic_open() is False


def test_обработчик_зовут_только_на_смене(клавиша):
    p, события = клавиша

    p.set_held(True)
    p.set_held(True)
    p.set_held(True)
    p.set_held(False)

    assert события == [True, False]


def test_упавший_обработчик_не_ломает_режим(monkeypatch):
    def падает(_):
        raise RuntimeError("окно закрылось")

    p = PushToTalk(on_change=падает)
    p.set_held(True)

    assert p.held is True, "состояние важнее обработчика"


def test_остановка_закрывает_микрофон(клавиша):
    """Иначе выключенный режим оставил бы микрофон открытым навсегда."""
    p, _ = клавиша
    p.set_held(True)
    p.stop()

    assert p.held is False


# ─── Честность про границы ────────────────────────────────────────────────────

def test_вне_windows_опрос_не_запускается(monkeypatch):
    monkeypatch.setattr(ptt.sys, "platform", "linux")
    p = PushToTalk()

    assert p.global_capable() is False
    assert p.start() is False, "нечему запускаться — и врать об этом не надо"


def test_про_ограничение_говорится_прямо(monkeypatch):
    """Молча неработающий PTT означает молчащий микрофон: человек будет
    говорить в пустоту и не поймёт почему."""
    monkeypatch.setattr(ptt.sys, "platform", "darwin")

    примечание = PushToTalk.scope_note()

    assert "в фокусе" in примечание
    assert "darwin" in примечание


def test_на_windows_режим_глобальный(monkeypatch):
    monkeypatch.setattr(ptt.sys, "platform", "win32")
    assert PushToTalk.global_capable() is True
    assert "из любого приложения" in PushToTalk.scope_note()


# ─── Настройка ────────────────────────────────────────────────────────────────

@pytest.fixture
def конфиг(monkeypatch):
    хранилище = {}
    from core import paths

    monkeypatch.setattr(paths, "load_api_keys", lambda: dict(хранилище))
    monkeypatch.setattr(paths, "save_api_keys", lambda d: хранилище.update(d))
    return хранилище


def test_по_умолчанию_режим_выключен(конфиг):
    """Для тихой комнаты пробуждение голосом удобнее."""
    assert ptt.enabled() is False


def test_настройка_сохраняется(конфиг):
    ptt.set_enabled(True)
    assert конфиг["push_to_talk"] is True
    assert ptt.enabled() is True


# ─── Связь с микрофонным гейтом ───────────────────────────────────────────────

def test_окно_умеет_ловить_аккорд():
    """Запасной путь для macOS и Linux — настоящие события клавиш."""
    from ui import MainWindow

    for имя in ("bind_push_to_talk", "keyPressEvent", "keyReleaseEvent"):
        assert callable(getattr(MainWindow, имя, None)), имя


def test_headless_покрывает_метод():
    """Без окна аккорд не ловится, но обращение к интерфейсу не должно падать."""
    from core.headless_ui import HeadlessUI

    HeadlessUI().bind_push_to_talk(lambda held: None)


def test_гейт_в_микрофоне_спрашивает_режим():
    """Проверка обязана стоять ДО отправки кадра, иначе звук уже ушёл."""
    import ast

    исходник = (_BASE / "main.py").read_text(encoding="utf-8")
    дерево = ast.parse(исходник)

    колбэк = next(
        узел for узел in ast.walk(дерево)
        if isinstance(узел, ast.FunctionDef) and узел.name == "callback"
    )
    текст = ast.unparse(колбэк)

    assert "_ptt.mic_open()" in текст
    # Гейт — раньше, чем кадр уезжает в очередь.
    assert текст.index("_ptt.mic_open()") < текст.index("_put_nowait_safe")


def test_предбуфер_чистится_при_закрытом_микрофоне():
    """Иначе первое же нажатие отправило бы полсекунды чужого разговора."""
    import ast

    исходник = (_BASE / "main.py").read_text(encoding="utf-8")
    дерево = ast.parse(исходник)
    колбэк = next(
        узел for узел in ast.walk(дерево)
        if isinstance(узел, ast.FunctionDef) and узел.name == "callback"
    )
    текст = ast.unparse(колбэк)

    кусок = текст[текст.index("_ptt.mic_open()"):]
    assert "preroll.clear()" in кусок[:400]
