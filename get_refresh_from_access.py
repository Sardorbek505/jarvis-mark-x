"""
Получение Spotify Refresh Token через Access Token

Использует access token для получения refresh token.
"""

import requests

# Access token из примера
ACCESS_TOKEN = "BQCiX3DMrmXAVTcHeKhQRqK1sgSQTXIK6_7W3yxPhg7zs1WYNofK3xjkqyVVIACvMKKlffzApBZJZIS5HsyTrcToAh-nzzf4n9XGXig-GpPKk1z6NMKCeXbA-e7Zlpp7E7u07IXpEyi_spDy-0rYt5vErHo0DH1i5AgTcuSxVFI6Fmza-kuQIw5cFo8CT-yhuEmfmiCYI5C7CYzyLz2MXGF2drlbZTkhBxtpq8otcC7TGkJsjHFtwSenrqdw9Bf6qz_J2jtl2xk4ftZmrbOHIZxpyC8ZiDex31vcxBMHiqFE83LYxB37NrK1cfAWv-fQ3v8Cb59Dyw"

# Credentials из config/api_keys.json
CLIENT_ID = "0b0e82a6a9614ed7b4f7c8297ba6f0bb"
CLIENT_SECRET = "6e97a6c397d64445873f4b4c2d83f3d0"

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
            print(f"Refresh Token:")
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
