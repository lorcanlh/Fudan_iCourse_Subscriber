"""Run on your own computer, never on a public Actions runner.

pip install google-auth-oauthlib
python scripts/authorize_drive.py /path/to/desktop-client.json
"""
import argparse
import json
import os
from pathlib import Path


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("client_json", type=Path)
    parser.add_argument("--output", type=Path, default=Path("drive-oauth.secret.json"))
    args = parser.parse_args()
    flow = InstalledAppFlow.from_client_secrets_file(
        str(args.client_json), scopes=["https://www.googleapis.com/auth/drive.file"])
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise RuntimeError("No refresh token returned; grant offline access again")
    data = {"client_id": creds.client_id, "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token}
    # Exclusive creation protects an existing credential file from overwrites.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(data, out)
    print(f"Saved credentials locally to {args.output}. Paste its contents into GitHub Secret GOOGLE_DRIVE_OAUTH_JSON; never commit it.")


if __name__ == "__main__":
    main()
