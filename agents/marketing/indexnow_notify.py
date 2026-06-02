#!/usr/bin/env python3
"""IndexNow notification hook — notifies Bing/Yandex/Seznam of new/changed URLs.

Usage:
  python3 indexnow_notify.py --all        # Notify all sitemap URLs
  python3 indexnow_notify.py --url <url>  # Notify single URL
  python3 indexnow_notify.py --urls urls.txt  # Notify URLs from newline-separated file

This is called automatically after:
  - new blog post deployment (owned_content_amplification.py)
  - site content updates (Ralph-Site Capistrano deploy)
  - doorway-page consolidation re-crawl requests
"""

import argparse
import os
import sys
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("ERROR: requests library required. Install with: pip install requests")
    sys.exit(1)

INDEXNOW_KEY = "5a24f43feb830aca8fc9048320bafacf"
KEY_LOCATION = f"https://ralphworkflow.com/{INDEXNOW_KEY}.txt"
HOST = "ralphworkflow.com"
API_HOST = "api.indexnow.org"
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


def load_urls_from_sitemap():
    """Parse live sitemap.xml and return all URLs."""
    try:
        import xml.etree.ElementTree as ET
        r = requests.get(f"https://{HOST}/sitemap.xml", timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [loc.text for loc in root.findall(".//ns:url/ns:loc", ns)]
        return urls
    except Exception as e:
        print(f"ERROR loading sitemap: {e}")
        return []


def notify_urls(urls: list[str]) -> dict:
    """Push URLs to IndexNow. Returns result dict."""
    result = {"ok": False, "submitted": 0, "total": len(urls), "status_code": None, "error": None}

    if not urls:
        result["error"] = "No URLs to submit"
        return result

    payload = {
        "host": HOST,
        "key": INDEXNOW_KEY,
        "keyLocation": KEY_LOCATION,
        "urlList": urls[:100],  # IndexNow max 100 per batch
    }

    try:
        r = requests.post(f"https://{API_HOST}/IndexNow", json=payload, timeout=15)
        result["status_code"] = r.status_code
        if r.status_code in (200, 202):
            result["ok"] = True
            result["submitted"] = len(payload["urlList"])
        else:
            result["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        result["error"] = str(e)

    return result


def notify_all_sitemap_urls() -> dict:
    """Notify all URLs in current sitemap. Batched at 100 per request (IndexNow limit)."""
    urls = load_urls_from_sitemap()
    if not urls:
        return {"ok": False, "error": "No URLs from sitemap"}

    all_results = []
    batch_size = 100
    for i in range(0, len(urls), batch_size):
        batch = urls[i : i + batch_size]
        r = notify_urls(batch)
        all_results.append(r)

    return {
        "ok": all(r["ok"] for r in all_results if r.get("ok") is not None),
        "batches": len(all_results),
        "submitted": sum(r.get("submitted", 0) for r in all_results),
        "total_sitemap_urls": len(urls),
        "results": all_results,
    }


def log_result(result: dict):
    """Write result to daily JSON log."""
    os.makedirs(LOG_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"indexnow_{today}.json")
    
    existing = []
    if os.path.exists(log_path):
        try:
            with open(log_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    existing.append(entry)
    
    with open(log_path, "w") as f:
        json.dump(existing, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Notify search engines (Bing/Yandex/Seznam) of new/changed URLs via IndexNow"
    )
    parser.add_argument("--all", action="store_true", help="Notify all sitemap URLs")
    parser.add_argument("--url", type=str, help="Single URL to notify")
    parser.add_argument("--urls", type=str, help="File with URLs (one per line)")
    parser.add_argument("--dry-run", action="store_true", help="Prepare but don't send")
    args = parser.parse_args()

    if args.all:
        if args.dry_run:
            urls = load_urls_from_sitemap()
            print(json.dumps({"dry_run": True, "urls": len(urls), "sample": urls[:5]}, indent=2))
            return
        result = notify_all_sitemap_urls()
    elif args.url:
        result = notify_urls([args.url])
    elif args.urls:
        with open(args.urls) as f:
            urls = [line.strip() for line in f if line.strip()]
        result = notify_urls(urls)
    else:
        parser.print_help()
        sys.exit(1)

    log_result(result)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
