#!/usr/bin/env python3
"""Check PRAW readiness without posting — reports credential status.
Run this before any Reddit-related work to avoid wasting cycles on dead lanes.

Exit codes:
  0 = ready (credentials configured, PRAW available)
  1 = credentials not configured (need human setup)
  2 = PRAW not installed
  3 = module import broken
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
sys.path.insert(0, str(ROOT))

def main():
    result = {
        "ready": False,
        "praw_installed": False,
        "credentials_configured": False,
        "account": None,
        "action": None,
    }

    # Check PRAW installation
    try:
        import praw  # noqa: F401
        result["praw_installed"] = True
    except ImportError:
        result["action"] = "pip install praw"
        print(json.dumps(result))
        sys.exit(2)

    # Check module import
    try:
        from agents.marketing.reddit_praw_post import read_reddit_credentials, read_account_username
    except Exception as e:
        result["action"] = f"Module import broken: {e}"
        print(json.dumps(result))
        sys.exit(3)

    result["account"] = read_account_username()

    # Check credentials
    try:
        client_id, client_secret, username = read_reddit_credentials()
        if client_id and client_secret and client_id != "<paste" and client_secret != "<paste":
            result["credentials_configured"] = True
            result["ready"] = True
            result["action"] = "Ready for Reddit posting via PRAW"
        else:
            result["action"] = (
                "Credentials are placeholders. Set up at https://www.reddit.com/prefs/apps "
                "(script type). See agents/marketing/REDDIT_PRAW_SETUP.md"
            )
    except Exception as e:
        result["action"] = str(e)

    if result["ready"]:
        print(json.dumps(result))
        sys.exit(0)
    else:
        print(json.dumps(result))
        sys.exit(1)


if __name__ == "__main__":
    main()
