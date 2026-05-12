"""Test script to verify the fixes"""
import os
import sys
from pathlib import Path

# Test 1: Check if config file exists
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config" / "api_keys.json"

print(f"BASE_DIR: {BASE_DIR}")
print(f"CONFIG_FILE exists: {CONFIG_FILE.exists()}")
print(f"CONFIG_FILE path: {CONFIG_FILE}")

if CONFIG_FILE.exists():
    import json
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    print(f"Config has spotify_client_id: {'spotify_client_id' in config}")
    print(f"Config has spotify_refresh_token: {'spotify_refresh_token' in config}")

# Test 2: Test Spotify controller import
try:
    sys.path.insert(0, str(BASE_DIR))
    from actions.spotify_controller import SpotifyAPI
    api = SpotifyAPI()
    print(f"SpotifyAPI initialized: {api.controller is not None}")
    print(f"SpotifyAPI ready: {api.is_ready()}")
except Exception as e:
    print(f"SpotifyAPI error: {e}")

# Test 3: Test DPI fix
import platform
if platform.system() == "Windows":
    print("Windows detected - DPI fix should be applied in ui.py")
else:
    print(f"Not Windows: {platform.system()}")

print("\n[OK] All checks completed")
