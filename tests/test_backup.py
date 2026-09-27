"""Резервная копия: всё личное — в один файл под паролем и обратно; сессии
Telegram не попадают; неверный пароль и подмена — ошибка; перед
восстановлением текущее сохраняется; окно ведёт по шагам."""
import json
import os
import time
from pathlib import Path

import pytest

from core import backup as B


def _data(root: Path, tag: str):
    (root / "memory").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "macros.json").write_text(json.dumps({"tag": tag}), encoding="utf-8")
    (root / "study.json").write_text(json.dumps({"tag": tag}), encoding="utf-8")
    (root / "memory" / "data.json").write_text(json.dumps({"tag": tag}), encoding="utf-8")
    (root / "config" / "obsidian.json").write_text(json.dumps({"tag": tag}), encoding="utf-8")
    (root / "config" / "api_keys.json").write_text('{"gemini_api_key": "НЕ ИЗ ФАЙЛА"}', encoding="utf-8")
    (root / "jarvis_me.session").write_bytes(b"SECRET SESSION")
    (root / "config" / "userbot.session").write_bytes(b"SECRET SESSION")
    (root / "memory" / "notes.txt").write_text("не наш формат", encoding="utf-8")


def test_round_trip(tmp_path):
    src, dst = tmp_path / "old_pc", tmp_path / "new_pc"
    _data(src, "старый")
    res = B.create(tmp_path / "copy", "пароль123", root=src, keys={"gemini_api_key": "AIza-1", "empty": ""})
    path = Path(res["path"])
    assert path.name == "copy.jarvisbak" and res == {"path": str(path), "files": 4, "keys": 1}
    blob = path.read_bytes()
    assert blob.startswith(B.MAGIC) and b"SECRET" not in blob and "старый".encode() not in blob
    info = B.inspect(path, "пароль123")
    assert sorted(info["files"]) == ["config/obsidian.json", "macros.json", "memory/data.json", "study.json"]

    _data(dst, "новый")
    saved = {}
    out = B.restore(path, "пароль123", root=dst, save_keys=saved.update, safety_dir=tmp_path / "safety",
                    keys_now={})
    assert out["files"] == 4 and saved == {"gemini_api_key": "AIza-1", "empty": ""}
    for f in ("macros.json", "study.json", "memory/data.json", "config/obsidian.json"):
        assert json.loads((dst / f).read_text(encoding="utf-8"))["tag"] == "старый"
    assert (dst / "jarvis_me.session").read_bytes() == b"SECRET SESSION"          # сессии не тронуты
    # «передумал»: снимок до восстановления — рядом, тем же паролем, с новыми данными
    before = Path(out["before"])
    assert before.parent == tmp_path / "safety"
    B.restore(before, "пароль123", root=dst, save_keys=lambda k: None, safety_dir=tmp_path / "safety", keys_now={})
    assert json.loads((dst / "macros.json").read_text(encoding="utf-8"))["tag"] == "новый"


def test_wrong_password_and_tampering(tmp_path):
    _data(tmp_path / "d", "x")
    path = Path(B.create(tmp_path / "c", "пароль123", root=tmp_path / "d", keys={})["path"])
    with pytest.raises(B.BackupError, match="Неверный пароль"):
        B.inspect(path, "не тот")
    blob = bytearray(path.read_bytes())
    blob[-5] ^= 1
    path.write_bytes(bytes(blob))
    with pytest.raises(B.BackupError, match="повреждён"):
        B.restore(path, "пароль123", root=tmp_path / "d", save_keys=lambda k: None, keys_now={})
    (tmp_path / "x.jarvisbak").write_bytes(b"PK\x03\x04 zip")
    with pytest.raises(B.BackupError, match="не резервная копия"):
        B.inspect(tmp_path / "x.jarvisbak", "пароль123")


def test_short_password_and_bad_paths(tmp_path):
    with pytest.raises(B.BackupError, match="хотя бы"):
        B.create(tmp_path / "c", "123", root=tmp_path, keys={})
    for bad in ("../evil.json", "/etc/passwd", "a.session", "C:evil.json", "C:\\Windows\\evil.json",
                "C:/Windows/evil.json", "\\evil.json", "\\\\server\\share\\x", "memory\\..\\..\\x", ""):
        assert not B._safe(bad), bad
    assert B._safe("memory/data.json") and B._safe("macros.json")


def test_restore_skips_paths_outside_data_folder(tmp_path):
    """Подложенный архив с путями наружу: верное — восстанавливается, чужое — нет."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", "{}")
        z.writestr("api_keys.json", "{}")
        z.writestr("data/macros.json", "{}")
        for evil in ("data/../evil1.json", "data/C:evil2.json", "data/\\evil3.json", "data//abs/evil4.json"):
            z.writestr(evil, "PWNED")
    path = tmp_path / "evil.jarvisbak"
    path.write_bytes(B.encrypt(buf.getvalue(), "пароль123"))
    root = tmp_path / "data"
    root.mkdir()
    out = B.restore(path, "пароль123", root=root, save_keys=lambda k: None, keys_now={})
    assert out["files"] == 1 and (root / "macros.json").exists()
    assert not any(p.read_text(errors="ignore") == "PWNED" for p in tmp_path.rglob("*") if p.is_file())


def test_window(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    def wait(cond):
        for _ in range(600):
            app.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        return False
    import ui_backup
    _data(tmp_path / "d", "x")
    saved = {}
    dlg = ui_backup.BackupDialog(root=tmp_path / "d", keys={"gemini_api_key": "AIza-9"}, save_keys=saved.update)
    try:
        dlg.pw1.setText("пароль123")
        dlg.pw2.setText("другой")
        dlg.ask_save()
        assert "не совпадают" in dlg.save_status.text()
        dlg.pw2.setText("пароль123")
        assert dlg.check_passwords() is None
        dlg.save_to(tmp_path / "copy.jarvisbak")
        assert wait(lambda: "Сохранено" in dlg.save_status.text()), dlg.save_status.text()
        assert dlg.pw1.text() == ""                                            # пароль не висит в поле
        dlg.set_file(tmp_path / "copy.jarvisbak")
        assert not dlg.restore_btn.isEnabled()
        dlg.pw_in.setText("не тот")
        dlg.open_backup()
        assert wait(lambda: "Неверный пароль" in dlg.restore_status.text())
        dlg.pw_in.setText("пароль123")
        dlg.open_backup()
        assert wait(lambda: dlg.restore_btn.isEnabled()) and "файлов 4" in dlg.info.text()
        dlg.restore_now()
        assert wait(lambda: "Восстановлено" in dlg.restore_status.text()), dlg.restore_status.text()
        assert saved == {"gemini_api_key": "AIza-9"} and "ключей 1" in dlg.restore_status.text()
    finally:
        dlg.close()
