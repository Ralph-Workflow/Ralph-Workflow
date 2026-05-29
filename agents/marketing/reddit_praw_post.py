#!/usr/bin/env python3
"""ARCHITECTURALLY RETIRED 2026-05-28."""
import json as _json
import sys as _sys

if __name__ == '__main__':
    print(_json.dumps({'status': 'retired', 'reason': 'Reddit pipeline architecturally retired 2026-05-28'}))
    _sys.exit(0)

"""
reddit_praw_post.py — PRAW-based Reddit posting for RalphWorkflow

Uses Reddit's official API (PRAW) instead of headless Playwright.
Avoids platform-level headless detection.

Credentials: read from TOOLS.md under ### Reddit API (PRAW)
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

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
    """Read Reddit OAuth app credentials from TOOLS.md."""
    text = TOOLS_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"^### Reddit API \(PRAW\)\n(?P<section>.*?)(?=^### |\Z)",
        text,
        re.M | re.S,
    )
    if not match:
        raise RuntimeError(
            "Reddit API (PRAW) credentials not found in TOOLS.md. "
            "See agents/marketing/REDDIT_PRAW_SETUP.md for setup instructions."
        )
    section = match.group("section")

    client_id_match = re.search(r"\*\*Client ID:\*\*\s*(?:<([^>]+)>|([^\n]+))", section)
    client_secret_match = re.search(r"\*\*Client Secret:\*\*\s*(?:<([^>]+)>|([^\n]+))", section)
    redirect_match = re.search(r"\*\*Redirect URI:\*\*\s*(?:<([^>]+)>|([^\n]+))", section)

    if not client_id_match or not client_secret_match:
        raise RuntimeError(
            "Reddit PRAW credentials incomplete in TOOLS.md. "
            "Need: Client ID, Client Secret. "
            "See agents/marketing/REDDIT_PRAW_SETUP.md."
        )

    client_id = (client_id_match.group(1) or client_id_match.group(2) or "").strip()
    client_secret = (client_secret_match.group(1) or client_secret_match.group(2) or "").strip()
    redirect_uri = ((redirect_match.group(1) or redirect_match.group(2) or "http://localhost:8080").strip() if redirect_match else "http://localhost:8080")

    placeholders = {
        "<paste from reddit.com/prefs/apps>",
        "paste from reddit.com/prefs/apps",
        "<paste from above>",
        "paste from above",
        "",
    }
    if client_id.lower() in placeholders or client_secret.lower() in placeholders:
        raise RuntimeError(
            "Reddit PRAW credentials in TOOLS.md are still placeholders. "
            "Register the script app first, then rerun auth."
        )

    return (client_id, client_secret, redirect_uri)


def read_account_username() -> str:
    config = REDDIT_ACCOUNT_CONFIG
    if config.exists():
        import json as _json
        data = _json.loads(config.read_text())
        return data.get("expected_username", "Informal-Salt827")
    return "Informal-Salt827"


# -------------------------------------------------------------------------- #
# PRAW setup
# -------------------------------------------------------------------------- #

def get_reddit_instance() -> "praw.Reddit":
    """Authenticate and return a PRAW Reddit instance."""
    try:
        import praw
    except ImportError:
        raise RuntimeError("praw not installed. Run: pip install praw")

    client_id, client_secret, redirect_uri = read_reddit_credentials()
    username = read_account_username()

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        user_agent=f"RalphWorkflow/1.0 (u/{username})",
    )
    return reddit


def get_authenticated_instance() -> "praw.Reddit":
    """
    Return a PRAW instance with stored refresh token.
    Uses interactive OAuth flow if no stored token exists.
    """
    import praw
    import json

    client_id, client_secret, redirect_uri = read_reddit_credentials()
    username = read_account_username()

    token_path = ROOT / "agents/marketing/logs/reddit_praw_token.json"

    # Try to load existing refresh token
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
                # Verify it works
                reddit.user.me()
                return reddit
        except Exception:
            token_path.unlink()  # Clear bad token

    # Need to authenticate interactively
    reddit = get_reddit_instance()
    print(f"Open this URL to authorize:\n{reddit.auth.url(scopes=['identity', 'read', 'submit', 'edit', 'flair'], state='permanent', duration='permanent')}")
    code = input("Paste the code from the URL: ").strip()
    refresh_token = reddit.auth.authorize(code)
    token_path.write_text(json.dumps({"refresh_token": refresh_token}))
    print("Refresh token saved.")
    return reddit


# -------------------------------------------------------------------------- #
# Posting functions
# -------------------------------------------------------------------------- #

def post_to_subreddit(
    subreddit: str,
    title: str,
    body: str,
    reddit=None,
    dry_run: bool = False,
) -> Optional[dict]:
    """
    Submit a link or text post to a subreddit.

    Returns a dict with post info on success, None on failure/dry-run.
    """
    if reddit is None:
        reddit = get_authenticated_instance()

    if dry_run:
        print(f"[DRY RUN] Would post to r/{subreddit}:")
        print(f"  Title: {title}")
        print(f"  Body: {body[:200]}...")
        return None

    try:
        subreddit_obj = reddit.subreddit(subreddit)
        submission = subreddit_obj.submit(title=title, selftext=body)
        _log_post(
            {
                "type": "submission",
                "subreddit": subreddit,
                "title": title,
                "url": submission.url,
                "id": submission.id,
                "permalink": submission.permalink,
            }
        )
        return {
            "ok": True,
            "id": submission.id,
            "url": submission.url,
            "permalink": submission.permalink,
        }
    except Exception as e:
        _log_failure("submission", subreddit, str(e))
        return None


def reply_to_post(
    post_id: str,
    body: str,
    reddit=None,
    dry_run: bool = False,
) -> Optional[dict]:
    """
    Reply to an existing Reddit post (submission ID).

    Returns a dict with reply info on success, None on failure/dry-run.
    """
    if reddit is None:
        reddit = get_authenticated_instance()

    if dry_run:
        print(f"[DRY RUN] Would reply to {post_id}:")
        print(f"  Body: {body[:200]}...")
        return None

    try:
        submission = reddit.submission(id=post_id)
        comment = submission.reply(body)
        _log_post(
            {
                "type": "comment",
                "post_id": post_id,
                "comment_id": comment.id,
                "permalink": comment.permalink,
            }
        )
        return {
            "ok": True,
            "comment_id": comment.id,
            "permalink": comment.permalink,
        }
    except Exception as e:
        _log_failure("comment", post_id, str(e))
        return None


# -------------------------------------------------------------------------- #
# Logging
# -------------------------------------------------------------------------- #

def _log_post(payload: dict) -> None:
    import json
    payload["ts"] = datetime.utcnow().isoformat() + "+00:00"
    REDDIT_LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(REDDIT_LOG_JSONL, "a") as f:
        f.write(json.dumps(payload) + "\n")


def _log_failure(action: str, target: str, error: str) -> None:
    import json
    OUTREACH_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n### {stamp} — PRAW {action} failure\n- Target: {target}\n- Error: {error}\n"
    if OUTREACH_LOG.exists():
        OUTREACH_LOG.write_text(OUTREACH_LOG.read_text() + entry, encoding="utf-8")
    else:
        OUTREACH_LOG.write_text("# Outreach Log\n" + entry, encoding="utf-8")


# -------------------------------------------------------------------------- #
# CLI
# -------------------------------------------------------------------------- #

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RalphWorkflow Reddit PRAW posting")
    parser.add_argument("--test-connection", action="store_true", help="Test PRAW auth")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without posting")
    parser.add_argument("--subreddit", default="AI_Agents", help="Subreddit to post to")
    parser.add_argument("--title", required=False, help="Post title")
    parser.add_argument("--body", required=False, help="Post body")
    parser.add_argument("--post-id", dest="post_id", help="Reply to this post ID instead")
    parser.add_argument("--reply-body", dest="reply_body", help="Reply body")
    args = parser.parse_args()

    if args.test_connection:
        try:
            reddit = get_authenticated_instance()
            user = reddit.user.me()
            print(f"✓ PRAW connected as u/{user}")
        except Exception as e:
            print(f"✗ PRAW connection failed: {e}")
            sys.exit(1)
        sys.exit(0)

    if args.post_id:
        body = args.reply_body or input("Reply body: ")
        reddit = get_authenticated_instance()
        result = reply_to_post(args.post_id, body, reddit=reddit, dry_run=args.dry_run)
        if result:
            print(f"✓ Replied: {result['permalink']}")
        else:
            print("✗ Reply failed")
            sys.exit(1)
    elif args.subreddit and args.title and args.body:
        reddit = get_authenticated_instance()
        result = post_to_subreddit(
            args.subreddit, args.title, args.body,
            reddit=reddit, dry_run=args.dry_run
        )
        if result:
            print(f"✓ Posted: {result['permalink']}")
        else:
            print("✗ Post failed")
            sys.exit(1)
    else:
        parser.print_help()
