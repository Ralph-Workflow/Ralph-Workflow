#!/usr/bin/env python3
"""Submit a single URL to Google Indexing API using the service account."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SA_PATH = Path(__file__).parent / "gsc_service_account.json"
SCOPES = ["https://www.googleapis.com/auth/indexing"]

try:
    info = json.loads(SA_PATH.read_text())
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
except Exception as e:
    print(f"FATAL: Cannot load service account from {SA_PATH}: {e}")
    sys.exit(1)

service = build("indexing", "v3", credentials=credentials)

def notify_index(url: str, action: str = "URL_UPDATED") -> dict:
    body = {"url": url, "type": action}
    try:
        resp = service.urlNotifications().publish(body=body).execute()
        return {"ok": True, "notifyTime": resp.get("urlNotificationMetadata", {}).get("latestUpdate", {}).get("notifyTime", "?"), "url": resp.get("urlNotificationMetadata", {}).get("url", url), "action": action}
    except HttpError as e:
        err = json.loads(e.content.decode()) if e.content else {}
        return {"ok": False, "error_code": e.resp.status, "reason": err.get("error", {}).get("message", str(e))}
    except Exception as e:
        return {"ok": False, "error_code": "exception", "reason": str(e)}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 _url_notify.py <URL> [URL_UPDATED|URL_DELETED]")
        sys.exit(1)
    url = sys.argv[1]
    action = sys.argv[2] if len(sys.argv) > 2 else "URL_UPDATED"
    result = notify_index(url, action)
    print(json.dumps(result, indent=2))
