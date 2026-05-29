#!/usr/bin/env python3
"""ARCHITECTURALLY RETIRED 2026-05-28."""
import json as _json
import sys as _sys

if __name__ == '__main__':
    print(_json.dumps({'status': 'retired', 'reason': 'Reddit pipeline architecturally retired 2026-05-28'}))
    _sys.exit(0)

"""
reddit_praw_reply.py — PRAW reply helper for reddit_autopost.py

Extracts submission ID + subreddit from a Reddit URL and posts a reply via PRAW.
Drops in as a replacement for the Playwright-based reddit_post.py when PRAW
credentials are available in TOOLS.md.

Usage:
    python3 reddit_praw_reply.py <reddit-url> --body-file <path> [--dry-run]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOOLS_PATH = ROOT / "TOOLS.md"
REDDIT_ACCOUNT_CONFIG = ROOT / "agents/marketing/reddit_account.json"
REDDIT_LOG_JSONL = ROOT / "agents/marketing/logs/reddit_posts.jsonl"
OUTREACH_LOG = ROOT / "outreach-log.md"

# -------------------------------------------------------------------------- #
# Credential reading
# -------------------------------------------------------------------------- #

def read_reddit_credentials() -> tuple[str, str, str]:
    text = TOOLS_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"^### Reddit API \(PRAW\)\n(?P<section>.*?)(?=^### |\Z)",
        text,
        re.M | re.S,
    )
    if not match:
        raise RuntimeError(
            "Reddit API (PRAW) credentials not found in TOOLS.md. "
            "See agents/marketing/REDDIT_PRAW_SETUP.md."
        )
    section = match.group("section")
    client_id_m = re.search(r"\*\*Client ID:\*\*\s*(?:<([^>]+)>|([^\n]+))", section)
    client_secret_m = re.search(r"\*\*Client Secret:\*\*\s*(?:<([^>]+)>|([^\n]+))", section)
    redirect_m = re.search(r"\*\*Redirect URI:\*\*\s*(?:<([^>]+)>|([^\n]+))", section)
    if not client_id_m or not client_secret_m:
        raise RuntimeError("PRAW credentials incomplete in TOOLS.md.")
    client_id = (client_id_m.group(1) or client_id_m.group(2) or "").strip()
    client_secret = (client_secret_m.group(1) or client_secret_m.group(2) or "").strip()
    redirect_uri = (redirect_m.group(1) or redirect_m.group(2) or "http://localhost:8080").strip() if redirect_m else "http://localhost:8080"
    placeholders = {
        "<paste from reddit.com/prefs/apps>",
        "paste from reddit.com/prefs/apps",
        "<paste from above>",
        "paste from above",
        "",
    }
    if client_id.lower() in placeholders or client_secret.lower() in placeholders:
        raise RuntimeError("PRAW credentials in TOOLS.md are still placeholders.")
    return (client_id, client_secret, redirect_uri)


def read_account_username() -> str:
    config = REDDIT_ACCOUNT_CONFIG
    if config.exists():
        data = json.loads(config.read_text())
        return data.get("expected_username", "Informal-Salt827")
    return "Informal-Salt827"


# -------------------------------------------------------------------------- #
# PRAW instance
# -------------------------------------------------------------------------- #

def get_reddit() -> "praw.Reddit":
    import praw, json
    client_id, client_secret, redirect_uri = read_reddit_credentials()
    username = read_account_username()
    token_path = ROOT / "agents/marketing/logs/reddit_praw_token.json"

    if token_path.exists():
        try:
            token_data = json.loads(token_path.read_text())
            refresh_token = token_data.get("refresh_token")
            if refresh_token:
                reddit = praw.Reddit(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    refresh_token=refresh_token,
                    user_agent=f"RalphWorkflow/1.0 (u/{username})",
                )
                reddit.user.me()  # Verify
                return reddit
        except Exception:
            token_path.unlink()

    # Interactive auth
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        user_agent=f"RalphWorkflow/1.0 (u/{username})",
    )
    print(f"Open: {reddit.auth.url(['identity', 'read', 'submit', 'edit', 'flair'], 'permanent', 'permanent')}")
    code = input("Code: ").strip()
    refresh_token = reddit.auth.authorize(code)
    token_path.write_text(json.dumps({"refresh_token": refresh_token}))
    return reddit


# -------------------------------------------------------------------------- #
# URL parsing
# -------------------------------------------------------------------------- #

def parse_reddit_url(url: str) -> tuple[str, str]:
    """
    Parse a Reddit URL and return (subreddit, submission_id).
    
    Handles:
      https://www.reddit.com/r/AI_Agents/comments/1tk0p4o/...
      https://old.reddit.com/r/AI_Agents/comments/1tk0p4o/...
      https://reddit.com/r/AI_Agents/comments/1tk0p4o/...
      https://www.reddit.com/r/ClaudeCode/comments/abc123/_/def456  (comment)
    """
    # submission ID: after /comments/ and before the next /
    m = re.search(r"/comments/([a-z0-9]+)", url, re.IGNORECASE)
    if not m:
        raise ValueError(f"Cannot extract submission ID from URL: {url}")
    submission_id = m.group(1)
    
    # subreddit: after /r/ and before the next /
    m2 = re.search(r"/r/([a-zA-Z0-9_]+)/", url)
    if m2:
        subreddit = m2.group(1)
    else:
        subreddit = "unknown"
    
    return subreddit, submission_id


# -------------------------------------------------------------------------- #
# Posting
# -------------------------------------------------------------------------- #

def post_reply(url: str, body: str, dry_run: bool = False) -> dict:
    subreddit, submission_id = parse_reddit_url(url)
    
    if dry_run:
        return {"status": "dry_run", "url": url, "subreddit": subreddit}
    
    reddit = get_reddit()
    try:
        submission = reddit.submission(id=submission_id)
        comment = submission.reply(body)
        
        # Log
        payload = {
            "ts": f"{__import__('datetime').datetime.utcnow().isoformat()}+00:00",
            "type": "comment",
            "subreddit": subreddit,
            "post_id": submission_id,
            "comment_id": comment.id,
            "url": url,
            "permalink": comment.permalink,
        }
        REDDIT_LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with open(REDDIT_LOG_JSONL, "a") as f:
            f.write(json.dumps(payload) + "\n")
        
        # Outreach log
        _log_success(url, comment.permalink, subreddit)
        
        return {
            "status": "posted",
            "comment_url": comment.permalink,
            "subreddit": subreddit,
        }
    except Exception as e:
        _log_failure(url, str(e))
        return {"status": "error", "detail": str(e)}


def _log_success(thread_url: str, comment_url: str, subreddit: str) -> None:
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n### {stamp} — PRAW comment ({subreddit})\n- Thread: {thread_url}\n- Comment: {comment_url}\n"
    OUTREACH_LOG.parent.mkdir(parents=True, exist_ok=True)
    existing = OUTREACH_LOG.read_text() if OUTREACH_LOG.exists() else "# Outreach Log\n"
    OUTREACH_LOG.write_text(existing.rstrip() + entry + "\n", encoding="utf-8")


def _log_failure(url: str, error: str) -> None:
    import datetime
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n### {stamp} — PRAW failure\n- URL: {url}\n- Error: {error}\n"
    existing = OUTREACH_LOG.read_text() if OUTREACH_LOG.exists() else "# Outreach Log\n"
    OUTREACH_LOG.write_text(existing.rstrip() + entry + "\n", encoding="utf-8")


# -------------------------------------------------------------------------- #
# CLI
# -------------------------------------------------------------------------- #

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Reddit thread URL")
    parser.add_argument("--body-file", required=True, help="Path to file containing body text")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8").strip()
    result = post_reply(args.url, body, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") in ("posted", "dry_run") else 1)
