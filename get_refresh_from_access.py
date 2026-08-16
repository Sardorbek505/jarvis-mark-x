"""
Получение Spotify Refresh Token через Access Token

Использует access token для получения refresh token.
"""

import json
import os
import sys
from pathlib import Path

import requests

CONFIG_FILE = Path(__file__).resolve().parent / "config" / "api_keys.json"


def _load_credentials():
    """Загрузить креды Spotify из config/api_keys.json или переменных окружения."""
    config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)

    client_id = os.getenv("SPOTIFY_CLIENT_ID") or config.get("spotify_client_id")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET") or config.get("spotify_client_secret")

    if not client_id or not client_secret:
        print("❌ Не найдены spotify_client_id / spotify_client_secret.")
        print("   Заполните config/api_keys.json или задайте переменные окружения")
        print("   SPOTIFY_CLIENT_ID и SPOTIFY_CLIENT_SECRET.")
        sys.exit(1)

    return client_id, client_secret


CLIENT_ID, CLIENT_SECRET = _load_credentials()

def get_refresh_token_from_access():
    """Get refresh token using authorization flow."""
    
    # Создаём авторизационную ссылку
    from urllib.parse import urlencode
    
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": "http://127.0.0.1:8888/callback",
        "scope": "user-read-playback-state user-modify-playback-state user-read-currently-playing"
    }
    
    auth_url = f"https://accounts.spotify.com/authorize?{urlencode(params)}"
    
    print()
    print("=" * 60)
    print("AUTHORIZATION LINK:")
    print("=" * 60)
    print(auth_url)
    print("=" * 60)
    print()
    print("Instructions:")
    print("1. Copy the link above")
    print("2. Open it in browser")
    print("3. Login to Spotify")
    print("4. Click 'Allow'")
    print("5. Copy code from URL (after ?code=)")
    print("6. Paste code here:")
    
    auth_code = input("\nPaste code: ").strip()
    
    if auth_code:
        # Получаем refresh token
        data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "http://127.0.0.1:8888/callback",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        }
        
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data=data
        )
        
        if response.status_code == 200:
            token_data = response.json()
            print()
            print("=" * 60)
            print("SUCCESS!")
            print("=" * 60)
            print("Refresh Token:")
            print(token_data["refresh_token"])
            print()
            print("Add this to config/api_keys.json:")
            print(f'"spotify_refresh_token": "{token_data["refresh_token"]}"')
        else:
            print(f"Error: {response.status_code}")
            print(response.text)
    else:
        print("Code not entered")

if __name__ == "__main__":
    get_refresh_token_from_access()
