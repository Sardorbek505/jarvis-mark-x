"""Ключи, вписанные в окне «Ключи» / в установленном JARVIS.exe, лежат в
%APPDATA%/JARVIS — модули бота раньше читали только config/ рядом с кодом:
в exe Fish Audio «не был настроен», и Джарвис говорил встроенным голосом."""
import json
import sys


def _home(monkeypatch, tmp_path):
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path))
        d = tmp_path / "JARVIS"
    else:
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        d = tmp_path / ".config" / "JARVIS"
    d.mkdir(parents=True)
    return d


def test_user_dir_keys_win_and_repo_config_still_works(monkeypatch, tmp_path):
    from telegram_bot import local_keys
    repo = tmp_path / "repo.json"
    repo.write_text(json.dumps({"fish_api_key": "old", "gemini_api_key": "g1", "x": "only-repo"}), encoding="utf-8")
    assert local_keys.read(repo)["fish_api_key"] == "old"          # как раньше, из исходников
    user = _home(monkeypatch, tmp_path)
    (user / "api_keys.json").write_text(json.dumps({"fish_api_key": "new", "gemini_api_key": ""}), encoding="utf-8")
    keys = local_keys.read(repo)
    assert keys["fish_api_key"] == "new" and keys["gemini_api_key"] == "g1" and keys["x"] == "only-repo"
    assert local_keys.read(tmp_path / "нет.json")["fish_api_key"] == "new"      # exe: config/ пуст


def test_fish_voice_is_configured_in_exe(monkeypatch, tmp_path):
    from telegram_bot import tts_fish
    monkeypatch.delenv("FISH_API_KEY", raising=False)
    monkeypatch.setattr(tts_fish, "_CONFIG_FILE", tmp_path / "нет.json")
    monkeypatch.setattr(tts_fish, "_cfg_cache", (0.0, {}))
    user = _home(monkeypatch, tmp_path)
    assert not tts_fish.is_configured()
    (user / "api_keys.json").write_text(json.dumps({"fish_api_key": "fk"}), encoding="utf-8")
    monkeypatch.setattr(tts_fish, "_cfg_cache", (0.0, {}))          # ключ вписали в окне
    assert tts_fish.is_configured()
