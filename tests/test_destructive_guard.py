"""Необратимое — только после подтверждения.

В main.py стоял словарь «критических действий» и комментарий, обещающий
подтверждение, но кода не было: выключение компьютера и удаление файлов
выполнялись сразу. И это не теория — микрофон отдавал в модель всё, что
слышал в комнате, включая музыку, так что команда могла родиться из ниоткуда,
а computer_settings делает `shutdown /s /t 5` по-настоящему.

Проверяем предикат отдельно от голосовой сессии: тащить в оффлайн-сьют
sounddevice и Qt ради двух функций незачем.
"""
import ast
import types


def _load():
    src = open("main.py", encoding="utf-8").read()
    tree = ast.parse(src)
    mod = types.ModuleType("_guard")
    wanted = {"_action_of", "_is_destructive"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") in {"_DESTRUCTIVE", "_CONFIRM_WINDOW_SEC"} for t in node.targets):
            exec(compile(ast.Module([node], []), "guard", "exec"), mod.__dict__)
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.get_source_segment(src, node), "guard", "exec"), mod.__dict__)
    return mod


def test_shutdown_and_restart_need_confirmation():
    g = _load()
    for action in ("shutdown", "restart", "reboot", "выключи", "перезагрузи компьютер"):
        assert g._is_destructive("computer_control", {"action": action}) is True, action


def test_file_deletion_needs_confirmation():
    g = _load()
    for action in ("delete", "remove", "удалить"):
        assert g._is_destructive("files", {"action": action}) is True, action


def test_harmless_actions_run_without_asking():
    """Блокировка экрана обратима, громкость тем более — спрашивать незачем."""
    g = _load()
    for action in ("lock", "volume_up", "volume_down", "mute", "screenshot", "brightness_up"):
        assert g._is_destructive("computer_control", {"action": action}) is False, action


def test_other_tools_are_never_destructive():
    g = _load()
    for tool in ("open_app", "weather", "web_search", "browser", "obsidian"):
        assert g._is_destructive(tool, {"action": "delete"}) is False, tool


def test_confirmation_window_is_bounded():
    """Окно должно быть коротким: подтверждение из прошлого разговора не в счёт."""
    g = _load()
    assert 30 <= g._CONFIRM_WINDOW_SEC <= 300
