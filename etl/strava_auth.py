#!/usr/bin/env python3
"""One-time Strava OAuth setup. Writes ~/.ironman.env with credentials.

Run interactively:
    .venv/bin/python etl/strava_auth.py

Steps it walks you through:
  1. Paste Client ID + Client Secret (from https://www.strava.com/settings/api;
     set the app's Authorization Callback Domain to `localhost`).
  2. It opens the authorize URL in your browser; approve access.
  3. Browser redirects to http://localhost/?code=XXXX... — paste that code
     (or the whole URL) back here.
  4. It exchanges the code for a refresh token and writes ~/.ironman.env.
"""
import os
import re
import webbrowser
from pathlib import Path

import requests

ENV = Path.home() / ".ironman.env"


def main():
    cid = input("Strava Client ID: ").strip()
    secret = input("Strava Client Secret: ").strip()

    url = ("https://www.strava.com/oauth/authorize"
           f"?client_id={cid}&response_type=code&redirect_uri=http://localhost"
           "&approval_prompt=auto&scope=activity:read_all,profile:read_all")
    print(f"\nOpening browser:\n{url}\n")
    webbrowser.open(url)

    raw = input("Paste the code (or full redirected URL): ").strip()
    m = re.search(r"code=([a-f0-9]+)", raw)
    code = m.group(1) if m else raw

    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": cid, "client_secret": secret,
        "code": code, "grant_type": "authorization_code"}, timeout=30)
    r.raise_for_status()
    tok = r.json()

    ENV.write_text(
        f"export STRAVA_CLIENT_ID={cid}\n"
        f"export STRAVA_CLIENT_SECRET={secret}\n"
        f"export STRAVA_REFRESH_TOKEN={tok['refresh_token']}\n")
    os.chmod(ENV, 0o600)
    athlete = tok.get("athlete", {})
    print(f"\nAuthorized as {athlete.get('firstname','?')} {athlete.get('lastname','')}")
    print(f"Wrote {ENV} (chmod 600). Next:\n"
          "  source ~/.ironman.env\n"
          "  .venv/bin/python etl/strava_sync.py --days 3650")


if __name__ == "__main__":
    main()
