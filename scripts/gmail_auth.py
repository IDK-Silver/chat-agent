#!/usr/bin/env python3
"""One-shot script to obtain Gmail OAuth2 refresh token.

Usage:
    uv run python scripts/gmail_auth.py

See docs/dev/gmail-oauth-setup.md for full setup instructions.
"""

import urllib.parse
import webbrowser

import httpx

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
_SCOPE = "https://mail.google.com/"


def main() -> None:
    print("=== Gmail OAuth2 Setup ===\n")
    print("You need a Client ID and Client Secret from Google Cloud Console.")
    print("See docs/dev/gmail-oauth-setup.md for instructions.\n")

    client_id = input("Client ID: ").strip()
    if not client_id:
        print("Error: Client ID is required.")
        return

    client_secret = input("Client Secret: ").strip()
    if not client_secret:
        print("Error: Client Secret is required.")
        return

    # Build authorization URL
    params = {
        "client_id": client_id,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"

    print(f"\nOpening browser for authorization...\n")
    print(f"If the browser doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    code = input("Paste the authorization code here: ").strip()
    if not code:
        print("Error: Authorization code is required.")
        return

    # Exchange code for tokens
    print("\nExchanging code for tokens...")
    try:
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": _REDIRECT_URI,
            },
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"\nError: {e.response.status_code} - {e.response.text}")
        return

    data = resp.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        print(f"\nError: No refresh_token in response: {data}")
        return

    print("\n=== Success! Add these to your .env file: ===\n")
    print(f"GMAIL_CLIENT_ID={client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={refresh_token}")
    print()


if __name__ == "__main__":
    main()
