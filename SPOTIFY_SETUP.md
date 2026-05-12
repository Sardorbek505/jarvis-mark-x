# Spotify OAuth Setup Guide

This guide explains how to obtain Spotify credentials for JARVIS.

## Step 1: Create Spotify Application

1. Go to [https://developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Click "Create App"
3. Fill in:
   - App name: "JARVIS"
   - App description: "Russian voice assistant"
   - Redirect URI: `http://127.0.0.1:8888/callback`
   - API: Web API
4. Click "Save"

## Step 2: Get Credentials

After creating the app, you'll see:
- **Client ID**: Copy this
- **Client Secret**: Click "Show Client Secret" and copy this

## Step 3: Get Authorization Code

Open this URL in your browser (replace YOUR_CLIENT_ID):

```
https://accounts.spotify.com/authorize?client_id=YOUR_CLIENT_ID&response_type=code&redirect_uri=http://127.0.0.1:8888/callback&scope=user-read-playback-state+user-modify-playback-state+user-read-currently-playing+user-library-read+user-library-modify+user-top-read+user-read-recently-played+playlist-read-private+playlist-read-collaborative+playlist-modify-public+playlist-modify-private
```

You'll be redirected to Spotify login page. After logging in, you'll be redirected to:

```
http://127.0.0.1:8888/callback?code=AUTH_CODE_HERE...
```

Copy the `AUTH_CODE_HERE` part.

## Step 4: Get Refresh Token

Use this Python script to exchange the authorization code for a refresh token:

```python
import requests

client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"
auth_code = "AUTH_CODE_HERE"
redirect_uri = "http://127.0.0.1:8888/callback"

response = requests.post(
    "https://accounts.spotify.com/api/token",
    data={
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret
    }
)

token_data = response.json()
refresh_token = token_data["refresh_token"]
access_token = token_data["access_token"]

print(f"Refresh Token: {refresh_token}")
print(f"Access Token: {access_token}")
```

## Step 5: Configure JARVIS

Add to `config/api_keys.json`:

```json
{
  "gemini_api_key": "your_gemini_key",
  "os": "windows",
  "spotify_client_id": "YOUR_CLIENT_ID",
  "spotify_client_secret": "YOUR_CLIENT_SECRET",
  "spotify_redirect_uri": "http://127.0.0.1:8888/callback",
  "spotify_refresh_token": "YOUR_REFRESH_TOKEN"
}
```

## Step 6: Test

Start JARVIS and say:
- "Включи Imagine Dragons"
- "Что играет"
- "Громкость 50"

If everything is configured correctly, JARVIS will control Spotify via official API.

## Troubleshooting

**Problem:** "Spotify недоступен, сэр"
- Check that all credentials are correct in `config/api_keys.json`
- Verify redirect URI matches exactly (including http:// not https://)
- Try refreshing the refresh token

**Problem:** Token expired
- The system should auto-refresh, but if it fails, repeat Step 4 to get a new refresh token

**Problem:** "Открой Spotify и выбери устройство, сэр"
- Make sure Spotify desktop app is installed
- Open Spotify and ensure it's logged in
- The system should detect it automatically

## Security Notes

- Never commit `config/api_keys.json` to version control
- Never share your Client Secret
- Refresh tokens are long-lived but can be revoked from Spotify dashboard
