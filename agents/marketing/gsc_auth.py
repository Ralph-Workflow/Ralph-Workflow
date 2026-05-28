#!/usr/bin/env python3
"""
One-time Google Search Console OAuth setup.

Run this once after you've created OAuth credentials in Google Cloud Console:
  python3 gsc_auth.py

It will:
1. Ask for your Client ID and Client Secret
2. Print an authorization URL
3. Ask you to visit the URL, authorize, and paste back the code
4. Store your refresh token securely in TOOLS.md

After this, seo_daily.py will automatically use GSC for rank tracking.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Add workspace to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
TOKEN_PATH = Path(__file__).parent / "gsc_token.json"
CLIENT_SECRETS_PATH = Path(__file__).parent / "gsc_client_secrets.json"


def get_client_config() -> dict:
    """Ask the user for their OAuth client credentials."""
    print("\n=== Google Search Console OAuth Setup ===\n")
    print("You need OAuth credentials from Google Cloud Console:")
    print("  1. Go to https://console.cloud.google.com")
    print("  2. Create a project (or use existing)")
    print("  3. Enable 'Google Search Console API'")
    print("  4. APIs & Services > Credentials > Create Credentials > OAuth Client ID")
    print("  5. Application type: 'Desktop app'")
    print("  6. Copy the Client ID and Client Secret\n")

    client_id = input("Paste your Client ID: ").strip()
    client_secret = input("Paste your Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Client ID and Client Secret are required.")
        sys.exit(1)

    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Installing required package...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "google-auth-oauthlib", "--quiet"], check=True)
        from google_auth_oauthlib.flow import InstalledAppFlow

    # Load or request client config
    if CLIENT_SECRETS_PATH.exists():
        print(f"Using existing client config at {CLIENT_SECRETS_PATH}")
        client_config = json.loads(CLIENT_SECRETS_PATH.read_text())
        overwrite = input("Overwrite existing credentials? [y/N]: ").strip().lower()
        if overwrite == 'y':
            client_config = get_client_config()
            CLIENT_SECRETS_PATH.write_text(json.dumps(client_config, indent=2))
            print(f"\nSaved updated client config to {CLIENT_SECRETS_PATH}")
        else:
            print("Using existing credentials — proceeding to OAuth flow.\n")
    else:
        client_config = get_client_config()
        CLIENT_SECRETS_PATH.write_text(json.dumps(client_config, indent=2))
        print(f"\nSaved client config to {CLIENT_SECRETS_PATH}")

    # Run OAuth flow
    # Note: InstalledAppFlow will try to open a browser. Since we're on a server,
    # it will print the URL instead. The user visits it, authorizes, gets a code,
    # and pastes it back.
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    # For server/CLI environments, use the console / manual flow
    print("\nLaunching OAuth flow...")
    print("Note: If no browser opens automatically, I'll print the URL for you.\n")

    try:
        # Try automatic port (will fail on server, fall back to console)
        credentials = flow.run_local_server(port=0, open_browser=False, prompt='consent')
    except Exception:
        # Fall back to console mode
        credentials = flow.run_console(open_browser=False)

    # Extract and store refresh token
    refresh_token = credentials.refresh_token
    if not refresh_token:
        print("ERROR: Could not get a refresh token. Your credentials may be incorrect.")
        sys.exit(1)

    # Save the token
    token_data = {
        "refresh_token": refresh_token,
        "client_id": client_config["installed"]["client_id"],
        "client_secret": client_config["installed"]["client_secret"],
    }
    TOKEN_PATH.write_text(json.dumps(token_data, indent=2))
    print(f"\n✅ Refresh token saved to {TOKEN_PATH}")
    print("   You can now use GSC rank tracking in seo_daily.py!")

    # Update TOOLS.md with a note
    tools_md = Path("/home/mistlight/.openclaw/workspace/TOOLS.md")
    if tools_md.exists():
        content = tools_md.read_text()
        if "GSC_REFRESH_TOKEN" not in content:
            note = (
                "\n## Google Search Console (GSC) Rank Tracking\n"
                "- **Status:** Configured ✅\n"
                "- **Token file:** `agents/marketing/gsc_token.json` (keep private!)\n"
                "- **Site tracked:** ralfworkflow.com\n"
                "- To re-authorize: `python3 agents/marketing/gsc_auth.py`\n"
            )
            tools_md.write_text(content + note)
            print(f"Updated {tools_md}")


if __name__ == "__main__":
    main()
