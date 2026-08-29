# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for building standalone JARVIS Mark X executable."""

import sys
from pathlib import Path

block_cipher = None

BASE_DIR = Path(SPECPATH)

datas = []
for p in ["config", "actions", "core", "face.png"]:
    full = BASE_DIR / p
    if full.exists():
        datas.append((str(full), p if full.is_dir() else "."))

if (BASE_DIR / "telegram_bot" / "miniapp").exists():
    datas.append((str(BASE_DIR / "telegram_bot" / "miniapp"), "telegram_bot/miniapp"))

hidden_imports = [
    "google.genai",
    "google.genai.types",
    "sounddevice",
    "_sounddevice_data",
    "edge_tts",
    "pydub",
    "psutil",
    "rapidfuzz",
    "feedparser",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "core",
    "core.brain",
    "core.proactive",
    "core.initiative",
    "core.emotions",
    "core.tray",
    "ui_setup",
    "telegram_bot.tts_edge",
    "telegram_bot.tts_fish",
    "telegram_bot.voice",
    "actions.obsidian",
    "actions.open_app",
    "actions.web_search",
    "actions.window_control",
    "actions.computer_settings",
    "actions.weather",
    "actions.music_player",
    "actions.spotify_controller",
]

a = Analysis(
    ['main.py'],
    pathex=[str(BASE_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'torch', 'torchvision', 'transformers'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JARVIS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No black console window — runs directly into GUI & Tray
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='JARVIS',
)
