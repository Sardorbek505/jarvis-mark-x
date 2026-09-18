"""Отмена собственных действий: стек и его правила.

Голосовой ассистент ослышивается, и до появления этого стека единственным
способом вернуть файл на место были руки. Проверяется не «функция вызвалась»,
а свойства, ради которых стек устроен именно так: запись снимается ДО
выполнения, глубина ограничена, сорвавшаяся регистрация не роняет действие.
"""

import sys
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from core import undo


@pytest.fixture(autouse=True)
def чистый_стек():
    undo.clear()
    yield
    undo.clear()


def test_пустой_стек_говорит_об_этом_человеческой_фразой():
    assert undo.can_undo() is False
    ответ = undo.undo_last()
    assert "Отменять нечего" in ответ
    # И объясняет границы: это отмена действий Джарвиса, а не Ctrl+Z в окне.
    assert "сам" in ответ


def test_отменяет_последнее_и_снимает_его_со_стека():
    сделано = []
    undo.push_undo("громкость: -10", lambda: сделано.append("громче") or "ок")

    assert undo.peek() == "громкость: -10"
    ответ = undo.undo_last()

    assert сделано == ["громче"]
    assert "громкость: -10" in ответ
    assert undo.can_undo() is False


def test_порядок_обратный_самое_свежее_первым():
    undo.push_undo("первое", lambda: None)
    undo.push_undo("второе", lambda: None)
    undo.push_undo("третье", lambda: None)

    assert undo.history() == ["третье", "второе", "первое"]
    undo.undo_last()
    assert undo.history() == ["второе", "первое"]


def test_глубина_ограничена_а_выбрасывается_самое_старое():
    for i in range(undo.MAX_DEPTH + 5):
        undo.push_undo(f"шаг {i}", lambda: None)

    записи = undo.history()
    assert len(записи) == undo.MAX_DEPTH
    assert записи[0] == f"шаг {undo.MAX_DEPTH + 4}", "новое должно остаться"
    assert "шаг 0" not in записи, "старое выбрасываем, а не отказываем новому"


def test_упавший_откат_не_повторяется_бесконечно():
    """Запись снимается ДО выполнения: мир мог уйти вперёд, и второй заход
    по тому же откату будет падать ровно так же."""
    попытки = []

    def падает():
        попытки.append(1)
        raise FileNotFoundError("файла уже нет")

    undo.push_undo("перемещение отчёта", падает)
    ответ = undo.undo_last()

    assert "Не удалось отменить" in ответ
    assert "перемещение отчёта" in ответ
    assert undo.can_undo() is False
    assert undo.undo_last().startswith("Отменять нечего")
    assert len(попытки) == 1


def test_уточнение_от_отката_попадает_в_ответ():
    undo.push_undo("перемещение файла", lambda: "отчёт.pdf вернулся на место.")
    ответ = undo.undo_last()
    assert "отчёт.pdf вернулся на место." in ответ


def test_кривая_регистрация_не_роняет_действие():
    """Действие уже выполнилось успешно — сорвавшаяся запись отмены не повод
    возвращать пользователю ошибку."""
    undo.push_undo("не функция", None)          # не callable
    undo.push_undo("тоже не функция", "строка")
    assert undo.can_undo() is False


def test_clear_забывает_всё():
    undo.push_undo("что-то", lambda: None)
    undo.clear()
    assert undo.can_undo() is False


# ─── Отмена настроек: только настоящие обратные действия ──────────────────────

from actions import computer_settings as cs


def _записать(mode, value="10", os_name="Windows", monkeypatch=None):
    вызовы = []

    def притворщик(режим, значение, player, record=True):
        вызовы.append((режим, значение, record))
        return "ок"

    cs._register_inverse("громкость", притворщик, mode, value)
    return вызовы


def test_относительное_изменение_обращается(monkeypatch):
    monkeypatch.setattr(cs, "_OS", "Windows")
    вызовы = _записать("down", "10")

    assert undo.peek() == "громкость: -10"
    undo.undo_last()
    assert вызовы == [("up", "10", False)], "отмена не должна писать свою отмену"


def test_абсолютное_значение_в_стек_не_кладётся(monkeypatch):
    """Прежнего значения система не сообщает — откатывать нечего, кроме догадки."""
    monkeypatch.setattr(cs, "_OS", "Windows")
    _записать("set", "40")
    assert undo.can_undo() is False


def test_mute_на_macos_отмену_не_предлагает(monkeypatch):
    """Там это не переключатель: повторный вызов снова выключит звук."""
    monkeypatch.setattr(cs, "_OS", "Darwin")
    _записать("mute")
    assert undo.can_undo() is False


def test_mute_на_windows_и_linux_переключается(monkeypatch):
    for os_name in ("Windows", "Linux"):
        undo.clear()
        monkeypatch.setattr(cs, "_OS", os_name)
        вызовы = _записать("mute")
        assert undo.can_undo() is True, os_name
        undo.undo_last()
        assert вызовы == [("mute", "10", False)], os_name
