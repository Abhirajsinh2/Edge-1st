"""
Upstox OAuth2 login for the Edge 1st bot. Run once per trading day
(Upstox access tokens expire ~03:30 IST).

    python login.py

Opens the Upstox login page in your browser, runs a tiny local server on
127.0.0.1:8888 to catch the redirect, exchanges the code for an access
token, and writes it to  edge_1st/upstox_token.json  and  edge_1st/.env
(UPSTOX_ACCESS_TOKEN=...). data_upstox.py reads it from there.

Your app's redirect URI in the Upstox developer console MUST be exactly:
    http://127.0.0.1:8888/callback
"""

import http.server
import json
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

import config

REDIRECT_URI = getattr(config, "UPSTOX_REDIRECT_URI", "http://127.0.0.1:8888/callback")
AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
HERE = Path(__file__).parent


class _Handler(http.server.BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        if "code" in qs:
            _Handler.code = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body style='background:#0d1117;color:#3fb950;"
                             b"font-family:sans-serif;text-align:center;padding-top:80px'>"
                             b"<h2>Edge 1st &mdash; Upstox login OK</h2>"
                             b"<p>You can close this tab.</p></body></html>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *_):
        pass


def main():
    key, secret = config.UPSTOX_API_KEY, config.UPSTOX_API_SECRET
    if not key or not secret or key.startswith("PUT_"):
        print("ERROR: set UPSTOX_API_KEY / UPSTOX_API_SECRET in edge_1st/config.py "
              "(or as env vars) first.")
        return

    auth = f"{AUTH_URL}?response_type=code&client_id={key}&redirect_uri={REDIRECT_URI}"
    print("=" * 62)
    print("  Edge 1st — Upstox login")
    print("=" * 62)
    print(f"  Redirect URI (must match your Upstox app): {REDIRECT_URI}")
    print("  Opening browser… log in; the token saves automatically.")
    print("  Waiting up to 2 minutes.\n")

    server = http.server.HTTPServer(("127.0.0.1", 8888), _Handler)
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    webbrowser.open(auth)
    t.join(timeout=120)

    if not _Handler.code:
        print("ERROR: login timed out or was cancelled.")
        return

    print("  Got auth code — exchanging for access token…")
    r = requests.post(TOKEN_URL, data={
        "code": _Handler.code, "client_id": key, "client_secret": secret,
        "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
    }, headers={"Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"}, timeout=15)
    if not r.ok:
        print(f"  ERROR: {r.status_code} {r.text}")
        return

    data = r.json()
    (HERE / "upstox_token.json").write_text(json.dumps(data, indent=2))

    env = HERE / ".env"
    lines = []
    if env.exists():
        lines = [l for l in env.read_text().splitlines()
                 if not l.startswith("UPSTOX_ACCESS_TOKEN=")]
    lines.append(f"UPSTOX_ACCESS_TOKEN={data['access_token']}")
    env.write_text("\n".join(lines) + "\n")

    print("  Token saved -> upstox_token.json + .env")
    print("  Valid until ~03:30 IST tomorrow. Now run:  python edge_1st_bot.py")
    print("=" * 62)


if __name__ == "__main__":
    main()
