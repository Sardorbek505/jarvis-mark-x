"""Свои команды: фразы (со слотами), привязка к программе, шаги по порядку,
неудача вслух, паки в один клик, создание голосом и словами (ИИ), окно."""
import os
import threading
import time

import pytest

from core import macros as mc
from core import quick


@pytest.fixture
def store(tmp_path):
    fg = {"exe": "chrome.exe"}
    m = mc.Macros(tmp_path / "macros.json", foreground=lambda: fg["exe"])
    done = []
    m.do = {k: (lambda s, k=k: done.append((k, s.get("value"), s.get("args")))) for k in mc.STEP_TYPES}
    said, logs = [], []
    m.say, m.log = said.append, logs.append
    return m, fg, done, said, logs


def test_first_run_installs_windows_and_browser_packs(store, tmp_path):
    m, *_ = store
    assert m.installed == {"windows", "browser"}
    again = mc.Macros(tmp_path / "macros.json")
    assert again.installed == {"windows", "browser"} and len(again.commands) == len(m.commands)


def test_browser_commands_only_when_browser_is_in_front(store):
    m, fg, *_ = store
    c, slots = m.match("Джарвис, новая вкладка")
    assert c.name == "Новая вкладка" and slots == {}
    fg["exe"] = "telegram.exe"
    assert m.match("новая вкладка") is None
    assert m.match("скопируй")[0].name == "Копировать"          # Windows-пак — везде


def test_slots_and_number_words_in_keys(store):
    m, fg, done, *_ = store
    c, slots = m.match("перейди на вкладку три")
    assert slots == {"номер": "три"}
    assert m.run(c, slots, wait=True) == "Готово."
    assert done == [("keys", "ctrl+3", None)]
    c, slots = m.match("найди на странице погода в москве")
    done.clear()
    m.run(c, slots, wait=True)
    assert done == [("keys", "ctrl+f", None), ("wait", "0.3", None), ("type", "погода в москве", None)]


def test_own_command_runs_steps_in_order_in_background(store):
    m, fg, done, said, logs = store
    cmd = mc.Command("Режим стрима", ["включи режим стрима", "режим стрима"], mc.clean_steps([
        {"do": "open_app", "value": "OBS Studio"}, {"do": "wait", "value": "2"},
        {"do": "keys", "value": "ctrl+shift+s"}, {"do": "tool", "tool": "music_player",
                                                   "args": '{"action": "play", "query": "{что}"}'},
        {"do": "bogus", "value": "x"}]))
    assert [s["do"] for s in cmd.steps] == ["open_app", "wait", "keys", "tool"]   # мусор отброшен
    m.upsert(cmd)
    hit = m.match("включи режим стрима")
    assert hit and hit[0].name == "Режим стрима"
    assert m.run(*hit) == "Выполняю «Режим стрима»."                        # ответ — сразу
    for _ in range(100):
        if logs:
            break
        time.sleep(0.01)
    assert [d[0] for d in done] == ["open_app", "wait", "keys", "tool"]
    assert logs == ["SYS: ✓ «Режим стрима» — выполнено"] and not said


def test_failed_step_stops_and_is_said(store):
    m, fg, done, said, logs = store

    def broken(s):
        raise RuntimeError("не нашёл программу OBS")
    m.do["open_app"] = broken
    cmd = m.upsert(mc.Command("Стрим", ["стрим"], mc.clean_steps([
        {"do": "open_app", "value": "OBS"}, {"do": "keys", "value": "ctrl+s"}])))
    assert "не выполнилась" in m.run(cmd, wait=True)
    assert done == [] and "шаге 1" in said[0] and "не нашёл программу OBS" in said[0]
    m.do["tool"] = lambda s: "Spotify сейчас не играет"
    m.upsert(mc.Command("Музыка", ["музыку"], [{"do": "tool", "tool": "music_player", "args": {}, "value": ""}]))
    assert "не выполнилась" in m.run(m.find("Музыка"), wait=True)      # «не играет» — это неудача


def test_voice_tool_create_list_show_delete_and_packs(store, monkeypatch):
    m, *_ = store
    monkeypatch.setattr(mc, "_macros", m)
    r = mc.macro_tool({"action": "create", "name": "Утро", "phrases": ["доброе утро"],
                       "steps": [{"do": "tool", "tool": "weather", "args": "{}"},
                                 {"do": "say", "value": "Хорошего дня"}]})
    assert r.startswith("Команда «Утро» сохранена: скажите «доброе утро».")
    assert "«Утро»" in mc.macro_tool({"action": "list"})
    assert "доброе утро" in mc.macro_tool({"action": "show", "name": "утро"})
    assert "Не понял шаги" in mc.macro_tool({"action": "create", "name": "x", "steps": []})
    assert mc.macro_tool({"action": "install_pack", "name": "фотошоп"}).startswith("Нет такого пака")
    assert "установлен" in mc.macro_tool({"action": "install_pack", "name": "Photoshop"})
    assert "✓ Photoshop" in mc.macro_tool({"action": "packs"})
    assert "удалён" in mc.macro_tool({"action": "remove_pack", "name": "photoshop"})
    assert mc.macro_tool({"action": "delete", "name": "Утро"}) == "Удалил."


def test_quick_path_runs_own_commands_but_asks_for_confirm_ones(store, monkeypatch):
    m, *_ = store
    monkeypatch.setattr(mc, "_macros", m)
    m.upsert(mc.Command("Выключить всё", ["выключи всё"], [{"do": "keys", "value": "alt+f4"}], confirm=True))
    m.upsert(mc.Command("Кино", ["режим кино"], [{"do": "volume", "value": "80"}]))
    q = quick.match("Джарвис, режим кино")
    assert q.tool == "macro" and q.args == {"action": "run", "phrase": "Джарвис, режим кино"}
    assert quick.match("выключи всё") is None                          # решит Gemini, с вопросом
    import main
    assert main._is_destructive("macro", {"action": "run", "name": "Выключить всё"})
    assert not main._is_destructive("macro", {"action": "run", "name": "Кино"})


def test_keys_parsing():
    assert mc.parse_keys("ctrl+k s") == [[0x11, 0x4B], [0x53]]
    assert mc.parse_keys("ctrl+plus") == [[0x11, 0xBB]] and mc.parse_keys("win+.") == [[0x5B, 0xBE]]
    assert mc.parse_keys("F5") == [[0x74]]
    with pytest.raises(ValueError):
        mc.parse_keys("ctrl+щ")


def test_ai_answer_parsed_into_command():
    d = mc.parse_ai('```json\n{"name": "Стрим", "phrases": ["включи стрим"], "steps": ['
                    '{"do": "open_app", "value": "OBS Studio"}, {"do": "wait", "value": 2},'
                    '{"do": "tool", "tool": "music_player", "args": {"action": "play"}}]}\n```')
    assert d["name"] == "Стрим" and [s["do"] for s in d["steps"]] == ["open_app", "wait", "tool"]
    assert d["steps"][1]["value"] == "2"
    with pytest.raises(ValueError):
        mc.parse_ai('{"name": "x", "steps": []}')


def test_every_pack_phrase_compiles_and_keys_are_known():
    from core.macro_packs import PACKS
    for key, pack in PACKS.items():
        for c in pack["commands"]:
            for p in c["phrases"]:
                mc.phrase_regex(p)
            for s in c["steps"]:
                if s["do"] == "keys" and "{" not in s["value"]:
                    mc.parse_keys(s["value"])


def test_editor_window(store):
    """Окно: список — только свои команды, ИИ заполняет рецепт, фразы-чипсы,
    шаги лентой (действие Джарвиса — из списка, без JSON), паки отдельно."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui_macros
    m, *_ = store
    built = threading.Event()
    dlg = ui_macros.MacrosDialog(store=m, build=lambda text: (built.set(), mc.parse_ai(
        '{"name": "Стрим", "phrases": ["включи стрим"], "steps": [{"do": "open_app", "value": "OBS"},'
        '{"do": "wait", "value": "2"}, {"do": "tool", "tool": "music_player",'
        ' "args": {"action": "play", "query": "lofi"}}]}'))[1])
    try:
        assert dlg.cmd_list.count() == 0 and dlg.empty.isVisibleTo(dlg)   # паки в список не лезут
        dlg.ai_text.setText("открой OBS, подожди 2 секунды и включи lofi")
        dlg.ask_ai()
        for _ in range(200):
            app.processEvents()
            if dlg.name.text() == "Стрим":
                break
            time.sleep(0.01)
        assert built.is_set() and len(dlg.step_rows) == 3
        tool_row = dlg.step_rows[2]
        assert tool_row.combo.currentText() == "Включить музыку" and tool_row.value.text() == "lofi"
        assert not tool_row.args.isVisibleTo(tool_row)                   # JSON спрятан
        dlg.phrase_in.setText("режим стрима")
        dlg.add_phrase(dlg.phrase_in.text())
        assert dlg.phrases() == ["включи стрим", "режим стрима"]
        dlg._move_step(dlg.step_rows[1], -1)                              # пауза — первой
        assert [r.number for r in dlg.step_rows] == [1, 2, 3] and dlg.step_rows[0].kind == "wait"
        assert dlg.save_command()
        assert m.match("режим стрима")[0].name == "Стрим" and dlg.cmd_list.count() == 1
        assert dlg.read_steps()[2] == {"do": "tool", "value": "", "tool": "music_player",
                                       "args": {"action": "play", "query": "lofi"}}
        dlg.new_command()
        assert not dlg.save_command() and "фразу" in dlg.status.text()   # подсказывает, чего не хватает
        dlg.toggle_pack("discord")
        assert "discord" in m.installed and dlg.pack_btns["discord"].text().strip() == "Установлен"
        dlg.toggle_pack("discord")
        assert "discord" not in m.installed
        dlg.repaint()
    finally:
        dlg.close()


def test_одноимённая_команда_не_затирает_старую(store):
    """Было: новая «Стрим» молча удаляла прежнюю «Стрим»."""
    m, *_ = store
    first = m.upsert(mc.Command("Стрим", ["режим стрима"], [{"do": "open_url", "value": "twitch.tv"}]))
    second = m.upsert(mc.Command("Стрим", ["открой ютуб"], [{"do": "open_url", "value": "youtube.com"}]))
    names = [c.name for c in m.commands]
    assert "Стрим" in names and "Стрим (2)" in names
    assert first.name == "Стрим" and second.name == "Стрим (2)"
    # Повторное сохранение той же команды не плодит «(3)».
    assert m.upsert(second).name == "Стрим (2)"


def test_своя_команда_не_затирает_встроенную_из_пака(store):
    m, *_ = store
    pack_back = [c for c in m.commands if c.pack and _norm_name(c.name) == "назад"]
    assert pack_back, "в паке браузера есть «Назад»"
    app = pack_back[0].app
    m.upsert(mc.Command("Назад", ["назад пожалуйста"], [{"do": "keys", "value": "alt+left"}], app=app))
    assert pack_back[0] in m.commands


def _norm_name(s):
    return " ".join(s.lower().split())
