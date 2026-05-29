#!/usr/bin/env python3
"""Submit a URL to the Google Indexing API for faster crawl/indexing."""
import json
import sys
import urllib.request
import urllib.error
import os
import time

GOOGLE_CLIENT_SECRETS = os.path.join(os.path.dirname(__file__), "gsc_client_secrets.json")
GOOGLE_TOKEN = os.path.join(os.path.dirname(__file__), "gsc_token.json")
GOOGLE_SERVICE_ACCOUNT = os.path.join(os.path.dirname(__file__), "gsc_service_account.json")


def _service_account_credentials():
    """Build service-account credentials from the JSON keyfile."""
    import google.auth
    from google.auth.transport.requests import Request as GAuthRequest
    try:
        creds = google.auth.load_credentials_from_file(
            GOOGLE_SERVICE_ACCOUNT,
            scopes=["https://www.googleapis.com/auth/indexing"],
        )[0]
        creds.refresh(GAuthRequest())
        return creds
    except Exception:
        return None


def _oauth_credentials():
    """Build and refresh OAuth credentials from the saved token."""
    import google.oauth2.credentials
    from google.auth.transport.requests import Request as GAuthRequest
    try:
        with open(GOOGLE_TOKEN) as f:
            tk = json.load(f)
        creds = google.oauth2.credentials.Credentials(
            token=tk.get("access_token"),
            refresh_token=tk.get("refresh_token"),
            token_uri=tk.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=tk.get("client_id"),
            client_secret=tk.get("client_secret"),
            scopes=tk.get("scopes", ["https://www.googleapis.com/auth/indexing"]),
        )
        if creds.expired or not creds.valid:
            creds.refresh(GAuthRequest())
            # Persist refreshed token
            with open(GOOGLE_TOKEN, "w") as f:
                json.dump({
                    "access_token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": creds.scopes,
                }, f)
        return creds
    except Exception:
        return None


def submit_url(url: str, action: str = "URL_UPDATED") -> dict:
    """Submit a URL to the Indexing API.

    Args:
        url: The URL to submit.
        action: "URL_UPDATED" (default for blog posts) or "URL_DELETED".
    """
    creds = _service_account_credentials() or _oauth_credentials()
    if not creds:
        return {"ok": False, "error": "No valid Google credentials found"}

    body = json.dumps({"url": url, "type": action}).encode("utf-8")
    req = urllib.request.Request(
        "https://indexing.googleapis.com/v3/urlNotifications:publish",
        data=body,
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            return {"ok": True, "url": url, "action": action, "response": result}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else str(e)
        return {"ok": False, "url": url, "error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


def main():
    urls = sys.argv[1:] if len(sys.argv) > 1 else sys.stdin.read().strip().splitlines()
    if not urls:
        print("Usage: submit_url_to_google.py <URL> [URL...]")
        print("       echo URL | submit_url_to_google.py")
        sys.exit(1)

    results = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        result = submit_url(url, "URL_UPDATED")
        results.append(result)
        status = "✓" if result["ok"] else "✗"
        error = result.get("error", "")
        print(f"  {status} {url} {error}")
        if result.get("response"):
            print(f"    notifyTime: {result['response'].get('urlNotificationMetadata', {}).get('latestUpdate', {}).get('notifyTime', 'N/A')}")

    ok = all(r["ok"] for r in results)
    print(f"\n{'OK' if ok else 'FAILED'}: {sum(1 for r in results if r['ok'])}/{len(results)} submitted")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
