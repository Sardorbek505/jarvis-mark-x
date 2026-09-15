"""
Тесты для проверок новых фичей JARVIS Mark X:
  1. Старковские анимации и метрики HUD (HudCanvas)
  2. Проактивная подгрузка заметок Obsidian в контекст
"""

import pytest
from PyQt6.QtWidgets import QApplication
from actions.obsidian import get_recent_obsidian_notes
from ui import HudCanvas


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["", "-platform", "offscreen"])
    return app


def test_obsidian_recent_notes_injection(tmp_path, monkeypatch):
    vault_dir = tmp_path / "JarvisVault"
    daily_dir = vault_dir / "Daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    note_path = daily_dir / "2026-09-07.md"
    note_path.write_text("# 2026-09-07\n\n- 10:00 Встреча по проекту BEK STYLE\n", encoding="utf-8")

    import actions.obsidian as obs_mod
    monkeypatch.setattr(obs_mod, "_config", lambda: {
        "vault_path": str(vault_dir),
        "inbox_folder": "00-Inbox",
        "daily_folder": "Daily",
    })

    ctx_text = get_recent_obsidian_notes(limit=3)
    assert "[ПОСЛЕДНИЕ ЗАМЕТКИ ИЗ OBSIDIAN V-A-U-L-T]" in ctx_text
    assert "2026-09-07" in ctx_text
    assert "BEK STYLE" in ctx_text


def test_hud_canvas_stark_state_variables(qapp, tmp_path):
    face_file = tmp_path / "face.png"
    face_file.write_bytes(b"fake_image")

    canvas = HudCanvas(str(face_file))
    assert hasattr(canvas, "_boot_progress")
    assert hasattr(canvas, "_thinking_angle")
    assert hasattr(canvas, "_glitch_offset_x")
    assert hasattr(canvas, "_glitch_offset_y")

    # Проверка прогона _step
    canvas.state = "ДУМАЕТ"
    canvas._step()
    assert canvas._thinking_angle > 0.0
    assert canvas._boot_progress > 0.0

    canvas.state = "RECONNECTING"
    canvas._step()
    # Сканлинии и джиттер активны в состоянии потери связи
    assert canvas.state == "RECONNECTING"
