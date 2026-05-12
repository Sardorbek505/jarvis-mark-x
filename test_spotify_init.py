"""
Тест инициализации Spotify API
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from actions.spotify_controller import spotify_api

print("=" * 60)
print("Testing Spotify API Initialization")
print("=" * 60)
print()

print("Checking if Spotify API is ready...")
if spotify_api.is_ready():
    print("SUCCESS! Spotify API is ready")
else:
    print("FAILED! Spotify API is not ready")
