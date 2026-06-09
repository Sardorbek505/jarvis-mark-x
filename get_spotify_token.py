"""
Получение Spotify Refresh Token для JARVIS

Этот скрипт автоматически получает refresh token через OAuth flow.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

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
    redirect_uri = (
        os.getenv("SPOTIFY_REDIRECT_URI")
        or config.get("spotify_redirect_uri")
        or "http://127.0.0.1:8888/callback"
    )

    if not client_id or not client_secret:
        print("❌ Не найдены spotify_client_id / spotify_client_secret.")
        print("   Заполните config/api_keys.json или задайте переменные окружения")
        print("   SPOTIFY_CLIENT_ID и SPOTIFY_CLIENT_SECRET.")
        sys.exit(1)

    return client_id, client_secret, redirect_uri


CLIENT_ID, CLIENT_SECRET, REDIRECT_URI = _load_credentials()

# Scopes для JARVIS
SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "user-read-email",
    "user-read-private",
    "user-library-read",
    "user-library-modify",
    "user-top-read",
    "playlist-read-private",
    "playlist-modify-public",
    "playlist-modify-private"
]

def get_auth_url():
    """Сгенерировать URL для авторизации."""
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES)
    }
    
    auth_url = f"https://accounts.spotify.com/authorize?{urlencode(params)}"
    return auth_url

def get_refresh_token(auth_code: str):
    """Получить refresh token по authorization code."""
    
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data=data
    )
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

def main():
    print("=" * 60)
    print("🎵 Получение Spotify Refresh Token для JARVIS")
    print("=" * 60)
    print()
    
    # Шаг 1: Показать URL для авторизации
    auth_url = get_auth_url()
    print("📋 ШАГ 1: Авторизация")
    print("-" * 60)
    print("1. Откройте эту ссылку в браузере:")
    print()
    print(auth_url)
    print()
    print("2. Войдите в свой Spotify аккаунт")
    print("3. Нажмите 'Allow' для предоставления доступа")
    print("4. Вы будете перенаправлены на:")
    print(f"   {REDIRECT_URI}?code=...")
    print()
    
    # Шаг 2: Получить authorization code
    auth_code = input("📝 Вставьте code из URL (после ?code=): ").strip()
    
    if not auth_code:
        print("❌ Не введён код авторизации")
        return
    
    print()
    print("🔄 ШАГ 2: Получение токена...")
    print("-" * 60)
    
    # Шаг 3: Получить refresh token
    token_data = get_refresh_token(auth_code)
    
    if token_data:
        print("✅ Success!")
        print()
        print("=" * 60)
        print("🔑 REFRESH TOKEN (скопируй в config/api_keys.json):")
        print("=" * 60)
        print()
        print(token_data["refresh_token"])
        print()
        print("=" * 60)
        print("📝 Инструкция:")
        print("=" * 60)
        print("1. Открой файл: config/api_keys.json")
        print("2. Замени значение 'spotify_refresh_token' на токен выше")
        print("3. Сохрани файл")
        print("4. Запусти JARVIS: python main.py")
        print("5. Протестируй: 'Jarvis play Miyagi love me'")
        print()
    else:
        print("❌ Не удалось получить токен")
        print("Убедитесь что:")
        print("- Введён правильный authorization code")
        print("- Redirect URI совпадает с настройками в Spotify Dashboard")
        print("- Client ID и Secret верны")

if __name__ == "__main__":
    main()
