"""
Тест Spotify аутентификации
"""

import json
import requests

# Load credentials
with open('config/api_keys.json', 'r') as f:
    config = json.load(f)

client_id = config['spotify_client_id']
client_secret = config['spotify_client_secret']
redirect_uri = config['spotify_redirect_uri']
refresh_token = config['spotify_refresh_token']

print("=" * 60)
print("Test Spotify Authentication")
print("=" * 60)
print()
print(f"Client ID: {client_id}")
print(f"Refresh Token: {refresh_token[:50]}...")
print()

# Try to refresh access token
print("1. Trying to get access token...")
data = {
    'grant_type': 'refresh_token',
    'refresh_token': refresh_token,
    'client_id': client_id,
    'client_secret': client_secret
}

try:
    response = requests.post('https://accounts.spotify.com/api/token', data=data)
    
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data.get('access_token')
        print(f"   Access token obtained: {access_token[:50]}...")
        
        # Save to tokens file
        tokens = {
            'access_token': access_token,
            'refresh_token': token_data.get('refresh_token', refresh_token),
            'expiry': None  # Will be set by controller
        }
        
        with open('config/spotify_tokens.json', 'w') as f:
            json.dump(tokens, f)
        
        print("   Tokens saved to config/spotify_tokens.json")
        print()
        print("=" * 60)
        print("SUCCESS! Spotify ready to use")
        print("=" * 60)
    else:
        print(f"   Error: {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"   Exception: {e}")
