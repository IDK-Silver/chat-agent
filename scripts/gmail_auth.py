#!/usr/bin/env python3
"""One-shot script to obtain Gmail OAuth2 refresh token.

Usage:
    uv run python scripts/gmail_auth.py

See docs/dev/gmail-oauth-setup.md for full setup instructions.
"""

import http.server
import threading
import urllib.parse
import webbrowser

import httpx

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://mail.google.com/"
_PORT = 8091


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

    # Start local server to receive the OAuth callback
    auth_code: str | None = None
    error: str | None = None

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            nonlocal auth_code, error
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            if "code" in params:
                auth_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h1>Authorization successful!</h1>"
                                 b"<p>You can close this tab.</p>")
            else:
                error = params.get("error", ["unknown"])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<h1>Error: {error}</h1>".encode())

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # Suppress request logs

    server = http.server.HTTPServer(("localhost", _PORT), _Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    redirect_uri = f"http://localhost:{_PORT}"

    # Build authorization URL
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("\nOpening browser for authorization...\n")
    print(f"If the browser doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for authorization...")
    thread.join(timeout=120)
    server.server_close()

    if error:
        print(f"\nError from Google: {error}")
        return
    if not auth_code:
        print("\nError: Timed out waiting for authorization.")
        return

    # Exchange code for tokens
    print("\nExchanging code for tokens...")
    try:
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": auth_code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
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
