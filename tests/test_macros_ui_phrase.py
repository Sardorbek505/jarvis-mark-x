"""Удаление фразы крестиком на чипе доходит до диска (раньше — только до окна)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import ui_macros as M  # noqa: E402
from core import macros as mc  # noqa: E402


def test_удаление_фразы_сохраняется(tmp_path):
    path = tmp_path / "macros.json"
    store = mc.Macros(path, foreground=lambda: "")
    d = M.MacrosDialog(store=store)
    d.new_command()
    d.name.setText("Музыка")
    d.add_phrase("включи лофи")
    d.add_phrase("лофи режим")
    d.add_step({"do": "open_url", "value": "lofi.com"})
    d.autosave.flush()

    d._remove_phrase("лофи режим")
    d.autosave.flush()
    saved = [c for c in mc.Macros(path).commands if c.name == "Музыка"]
    assert saved and saved[0].phrases == ["включи лофи"]
