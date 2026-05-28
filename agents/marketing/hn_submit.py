#!/usr/bin/env python3
"""Hacker News submission script for RalphWorkflow.

Submits the primary Codeberg repo URL to Hacker News.
Falls back to: if submission fails, log the packet for human handoff.

Usage:
  python3 hn_submit.py [--dry-run]

Environment:
  BROWSERLESS_TOKEN  — browserless API token for headless Chrome
  HN_USER / HN_PASS — HN account credentials (optional; attempts browserless if available)

Submission URL: https://codeberg.org/RalphWorkflow/Ralph-Workflow
Title: "Show RalphWorkflow: A composable AI coding workflow loop that runs overnight and hands back reviewable output"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
LOG_DIR = ROOT / "agents/marketing/logs"
OUTREACH_LOG = ROOT / "outreach-log.md"
HN_PACKET = ROOT / "drafts/HN_LOBSTERS_ACTIVE_PACKET.md"
BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN", "")
HN_USER = os.environ.get("HN_USER", "")
HN_PASS = os.environ.get("HN_PASS", "")

SUBMISSION_URL = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
SUBMISSION_TITLE = "Show RalphWorkflow: A composable AI coding workflow loop that runs overnight and hands back reviewable output"


def log_result(outcome: dict) -> None:
    """Append submission attempt to the outreach log."""
    ts = datetime.now(timezone.utc).isoformat()
    entry = f"\n## HN Submission Attempt — {ts}\n"
    entry += f"**Status:** {outcome['status']}\n"
    entry += f"**URL submitted:** {outcome.get('submitted_url', SUBMISSION_URL)}\n"
    entry += f"**Title:** {outcome.get('title', SUBMISSION_TITLE)}\n"
    if outcome.get('detail'):
        entry += f"**Detail:** {outcome['detail']}\n"
    if outcome.get('error'):
        entry += f"**Error:** {outcome['error']}\n"

    with open(OUTREACH_LOG, "a") as f:
        f.write(entry)


def submit_via_browserless() -> dict:
    """Attempt HN submission via browserless headless Chrome."""
    if not BROWSERLESS_TOKEN:
        return {"status": "skipped", "detail": "No BROWSERLESS_TOKEN"}

    import urllib.request

    cdp_url = f"https://chrome.browserless.io/devtools/browser?token={BROWSERLESS_TOKEN}"

    try:
        # Launch a browser session via browserless
        launch_req = urllib.request.Request(
            cdp_url,
            method="GET",
        )
        with urllib.request.urlopen(launch_req, timeout=15) as resp:
            browser_data = json.loads(resp.read())
            ws_endpoint = browser_data.get("wsEndpoint", "")
            if not ws_endpoint:
                return {"status": "error", "error": "No WebSocket endpoint from browserless"}
    except Exception as e:
        return {"status": "error", "error": f"Browserless launch failed: {e}"}

    # Fallback: use requests-based approach with Playwright
    # Since we have Playwright available (used by apollo_monitor), use that
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "skipped", "detail": "Playwright not available"}

    outcome = {"status": "unknown", "submitted_url": SUBMISSION_URL, "title": SUBMISSION_TITLE}

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect(ws_endpoint=ws_endpoint)
            page = browser.new_page()

            # Go to HN submit page
            page.goto("https://news.ycombinator.com/submit", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

            # Fill in the URL
            url_input = page.query_selector('input[name="url"]')
            if not url_input:
                outcome["status"] = "error"
                outcome["error"] = "Could not find URL input on HN submit page"
                browser.close()
                return outcome

            url_input.fill(SUBMISSION_URL)
            time.sleep(0.5)

            # Fill in the title
            title_input = page.query_selector('input[name="title"]')
            if title_input:
                title_input.fill(SUBMISSION_TITLE)

            time.sleep(0.5)

            # Click submit
            submit_btn = page.query_selector('input[type="submit"]')
            if submit_btn:
                submit_btn.click()

            page.wait_for_load_state("networkidle", timeout=10000)

            # Check for confirmation
            final_url = page.url
            if " HN " in final_url or page.query_selector(".title") or "submitted" in final_url:
                outcome["status"] = "success"
            else:
                outcome["status"] = "submitted_unconfirmed"
                outcome["detail"] = f"Final URL: {final_url}"

            browser.close()

    except Exception as e:
        outcome["status"] = "error"
        outcome["error"] = str(e)

    return outcome


def submit_via_api() -> dict:
    """Attempt HN submission via official HN API (Reader mode — limited)."""
    # HN doesn't have a public submit API. This is a placeholder for completeness.
    return {"status": "skipped", "detail": "HN has no public submission API — browserless or manual required"}


def prepare_packet() -> dict:
    """Prepare the HN submission packet."""
    packet = {
        "url": SUBMISSION_URL,
        "title": SUBMISSION_TITLE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "hn_submit.py",
        "packet_path": str(HN_PACKET),
        "notes": "Review HN_LOBSTERS_ACTIVE_PACKET.md before submitting. "
                 "HN rewards authentic, specific posts from real users. "
                 "Lead with what it actually does in one sentence. "
                 "No self-promotion fluff. Focus: overnight unattended project-scale AI coding work.",
    }

    # Check if packet exists
    if HN_PACKET.exists():
        with open(HN_PACKET) as f:
            content = f.read()
        packet["packet_content_preview"] = content[:500]
        packet["packet_exists"] = True
    else:
        packet["packet_exists"] = False

    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit RalphWorkflow to Hacker News")
    parser.add_argument("--dry-run", action="store_true", help="Prepare packet only, don't submit")
    args = parser.parse_args()

    print(f"[*] HN Submission for: {SUBMISSION_URL}")
    print(f"[*] Title: {SUBMISSION_TITLE}")

    packet = prepare_packet()
    print(f"[*] Packet ready: {packet['packet_exists']}")

    if args.dry_run:
        print("[*] Dry-run — logging and skipping submission")
        outcome = {**packet, "status": "dry_run", "detail": "Dry-run, no submission attempted"}
        log_result(outcome)
        print(json.dumps(outcome, indent=2))
        return 0

    # Attempt submission
    print("[*] Attempting submission...")

    # Priority: browserless (if credentials available)
    if BROWSERLESS_TOKEN and HN_USER and HN_PASS:
        outcome = submit_via_browserless()
    else:
        outcome = {
            "status": "manual_required",
            "detail": "No BROWSERLESS_TOKEN or HN credentials — manual submission required",
            "submitted_url": SUBMISSION_URL,
            "title": SUBMISSION_TITLE,
        }

    print(f"[*] Outcome: {outcome['status']}")
    if outcome.get("detail"):
        print(f"    Detail: {outcome['detail']}")
    if outcome.get("error"):
        print(f"    Error: {outcome['error']}")

    log_result(outcome)

    # Write submission result
    result_path = LOG_DIR / f"hn_submission_{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}.json"
    with open(result_path, "w") as f:
        json.dump({**packet, **outcome}, f, indent=2)
    print(f"[*] Result logged: {result_path}")

    return 0 if outcome["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
