# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for building standalone JARVIS Mark X executable.

КРИТИЧЕСКИ ВАЖНО: Никогда не паковать реальные секреты (api_keys.json, userbot.session,
jarvis_memory.db). Пакуются только безопасные шаблоны и ассеты.
"""

import sys
from pathlib import Path

block_cipher = None

BASE_DIR = Path(SPECPATH)

# Только безопасные шаблоны и ассеты (БЕЗ личных данных, токенов и сессий)
datas = []

safe_files = [
    ("core/prompt.txt", "core"),
    ("config/modes.json", "config"),
    ("config/obsidian.json", "config"),
    ("config/api_keys.example.json", "config"),
    ("face.png", "."),
    ("app.ico", "."),
]

for src, dst in safe_files:
    full = BASE_DIR / src
    if full.exists():
        datas.append((str(full), dst))

if (BASE_DIR / "assets").exists():
    datas.append((str(BASE_DIR / "assets"), "assets"))

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
    "core.paths",
    "core.emotion_analyzer",
    "core.proactive_engine",
    "core.initiative_engine",
    "core.team_collaboration",
    "core.user_profile",
    "core.latency",
    "core.wakeword",
    "core.tray",
    "ui_setup",
    "telegram_bot.tts_edge",
    "telegram_bot.tts_fish",
    "telegram_bot.voice",
    "actions.vision",
    "actions.telegram_sender",
    "actions.obsidian",
    "actions.open_app",
    "actions.web_search",
    "actions.window_control",
    "actions.computer_settings",
    "actions.weather",
    "actions.music_player",
    "actions.spotify_controller",
    "imageio_ffmpeg",
]

binaries = []
try:
    from PyInstaller.utils.hooks import collect_all
    ff_datas, ff_binaries, ff_hidden = collect_all("imageio_ffmpeg")
    datas += ff_datas
    binaries += ff_binaries
    hidden_imports += ff_hidden
except Exception:
    pass

a = Analysis(
    ['main.py'],
    pathex=[str(BASE_DIR)],
    binaries=binaries,
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
    icon=str(BASE_DIR / 'app.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
