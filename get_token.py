#!/usr/bin/env python3
"""Run ONCE on your own computer to get a YouTube refresh token.

1. Put the OAuth client file you downloaded from Google Cloud next to this
   script and name it client_secret.json.
2. pip install google-auth-oauthlib
3. python get_token.py  -> a browser opens, sign in with the channel's account.
4. Copy the three printed values into GitHub repository secrets.
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
path = Path(__file__).resolve().parent / "client_secret.json"

flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
client = json.loads(path.read_text())
client = client.get("installed") or client.get("web")

print("\nAdd these as GitHub repository secrets:\n")
print("YT_CLIENT_ID     =", client["client_id"])
print("YT_CLIENT_SECRET =", client["client_secret"])
print("YT_REFRESH_TOKEN =", creds.refresh_token)
