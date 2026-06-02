#!/usr/bin/env python3
"""Bing IndexNow bulk-submit — pings known Ralph Workflow pages to Bing/IndexNow.

IndexNow is a free, API-keyless protocol supported by Bing, Yandex, Seznam,
and others. Submitting URLs through IndexNow triggers crawl within hours for
participating engines — a genuinely new autonomous distribution surface that
requires no account, no captcha, and no publisher outreach.

This is the single highest-leverage zero-cost SEO move available right now:
IndexNow does not require Google Search Console access, Cloudflare, or any
blocked surface. It just needs a valid sitemap and an HTTP client.

Created 2026-05-31 as part of marketing-workflow-audit structural repair.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

ROOT = Path("/home/mistlight/.openclaw/workspace")
LOG_DIR = ROOT / "agents/marketing/logs"
STATE_PATH = LOG_DIR / "indexnow_state_latest.json"
SITEMAP_URL = "https://ralphworkflow.com/sitemap.xml"
INDEXNOW_ENDPOINTS = [
    "https://www.bing.com/indexnow",
    "https://api.indexnow.org/indexnow",
]
# IndexNow key: a persistent identifier for this site/project.
# Using a fixed key so engines recognize repeat submissions from the same owner.
INDEXNOW_KEY = "5a24f43feb830aca8fc9048320bafacf"
SITE_HOST = "ralphworkflow.com"
USER_AGENT = "RalphWorkflow-IndexNow/1.0 (https://ralphworkflow.com)"
# Extra blog/sitemap pages known from Hugo output but not always in sitemap.xml
KNOWN_EXTRA_PAGES = [
    "https://ralphworkflow.com/blog/",
    "https://ralphworkflow.com/compare/",
]
REQUEST_TIMEOUT = 20  # seconds
# Prevent multiple same-day submissions. Cron runs Mon/Thu 05:00 — if any
# other script path calls this, it should skip when a submission already
# happened today. IndexNow explicitly warns against over-pinging.
SAME_DAY_COOLDOWN_HOURS = 23


def _now_iso() -> str:
    return datetime.now().isoformat()


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {
        "last_submit_ts": None,
        "total_submitted": 0,
        "last_count": 0,
        "last_errors": [],
    }


def save_state(state: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def _fetch(url: str) -> str | None:
    """Fetch URL content with standard headers."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/xml, text/xml, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠️  Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def parse_sitemap_urls(xml_content: str) -> list[str]:
    """Extract <loc> URLs from a sitemap XML body."""
    urls: list[str] = []
    try:
        root = ElementTree.fromstring(xml_content)
        # Handle both default namespace and no-namespace
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for url_elem in root.findall(".//sm:url/sm:loc", ns) or root.findall(
            ".//url/loc"
        ):
            text = (url_elem.text or "").strip()
            if text and text.startswith("https://ralphworkflow.com"):
                urls.append(text)
    except ElementTree.ParseError as e:
        print(f"  ⚠️  Failed to parse sitemap XML: {e}", file=sys.stderr)
    return urls


def submit_urls_batch(urls: list[str], endpoint: str) -> dict[str, Any]:
    """Submit a batch of URLs to an IndexNow endpoint."""
    payload = json.dumps({
        "host": SITE_HOST,
        "key": INDEXNOW_KEY,
        "keyLocation": f"https://{SITE_HOST}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "body": body[:500]}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": e.code, "body": body[:500]}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e)[:500]}


def _same_day_submission_exists(now: datetime) -> bool:
    """Return True if a submission already happened within the cooldown window.

    Prevents over-pinging: IndexNow explicitly warns that excessive submissions
    risk rate-limiting or abuse flagging. If any caller already submitted within
    SAME_DAY_COOLDOWN_HOURS, skip to avoid duplicate pings.
    """
    state = load_state()
    last_ts = state.get("last_submit_ts")
    if not last_ts:
        return False
    try:
        last = datetime.fromisoformat(last_ts)
    except Exception:
        return False
    # Normalize both to UTC for safe comparison
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = now - last
    return delta.total_seconds() < (SAME_DAY_COOLDOWN_HOURS * 3600)


def run() -> None:
    """Main entry: fetch sitemap, extract URLs, submit to IndexNow endpoints."""
    print(f"=== IndexNow bulk-submit {_now_iso()} ===")

    now = datetime.now()
    if _same_day_submission_exists(now):
        state = load_state()
        last_ts = state.get("last_submit_ts", "unknown")
        print(f"  ⏭️  Skipping — last submission was at {last_ts} (within {SAME_DAY_COOLDOWN_HOURS}h cooldown)")
        sys.exit(0)

    state = load_state()

    # 1. Fetch sitemap
    print("  Fetching sitemap...")
    xml = _fetch(SITEMAP_URL)
    if not xml:
        print("  ❌ Cannot fetch sitemap — aborting")
        state["last_errors"] = ["sitemap_fetch_failed"]
        save_state(state)
        sys.exit(1)

    # 2. Parse URLs
    sitemap_urls = parse_sitemap_urls(xml)
    print(f"  Sitemap URLs found: {len(sitemap_urls)}")

    # 3. Add known extra pages
    all_urls = list(dict.fromkeys(sitemap_urls + KNOWN_EXTRA_PAGES))  # deduplicate
    print(f"  Total URLs to submit: {len(all_urls)}")
    if not all_urls:
        print("  ❌ No URLs extracted — aborting")
        state["last_errors"] = ["no_urls_in_sitemap"]
        save_state(state)
        sys.exit(1)

    # 4. Submit to each endpoint
    results = {}
    all_ok = True
    for endpoint in INDEXNOW_ENDPOINTS:
        print(f"  Submitting to {endpoint} ...")
        result = submit_urls_batch(all_urls, endpoint)
        label = endpoint.replace("https://", "").split("/")[0]
        results[label] = result
        if result["ok"]:
            print(f"    ✅ HTTP {result['status']}")
        else:
            print(f"    ❌ HTTP {result['status']} — {result['body'][:120]}")
            all_ok = False

    # 5. Update state
    state["last_submit_ts"] = _now_iso()
    state["total_submitted"] += len(all_urls)
    state["last_count"] = len(all_urls)
    state["last_results"] = results
    if all_ok:
        state["last_errors"] = []
    else:
        state["last_errors"] = [
            f"{label}: {r['status']}"
            for label, r in results.items()
            if not r["ok"]
        ]
    save_state(state)

    print(f"  Done. Cumulative total submitted: {state['total_submitted']}")
    if not all_ok:
        print(f"  ⚠️  Some endpoints failed: {state['last_errors']}")
        sys.exit(1)


if __name__ == "__main__":
    run()