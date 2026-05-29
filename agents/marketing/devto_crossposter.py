#!/usr/bin/env python3
"""Cross-post RalphWorkflow blog content to Dev.to via the Forem API.

This is a fresh distribution lane — none of the existing lanes target Dev.to,
which has a large developer audience with organic SEO juice.

Usage:
    DEVTO_API_KEY=your-key python3 agents/marketing/devto_crossposter.py --dry-run
    DEVTO_API_KEY=your-key python3 agents/marketing/devto_crossposter.py --post "blog/codex-opencode-cline-vs-ralph-workflow-2026"
    DEVTO_API_KEY=your-key python3 agents/marketing/devto_crossposter.py --mode check-queue

Requirements:
    - DEVTO_API_KEY environment variable (Forem API v1 key)
    - Already-published blog posts on ralphworkflow.com

By default, cross-posts use canonical_url back to ralphworkflow.com so SEO
weight accrues to the primary domain, not dev.to.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
SITE_DOMAIN = 'https://ralphworkflow.com'
DEVTO_API_BASE = 'https://dev.to/api'
DEVTO_ORG = 'ralph-workflow'  # optional org slug

QUEUE_PATH = ROOT / 'agents/marketing/logs/devto_crosspost_queue.json'
STATE_PATH = ROOT / 'agents/marketing/logs/devto_crosspost_state.json'
BLOG_CONTENT_DIR = ROOT / 'Ralph-Site/content/blog'

# Posts we can cross-post (blog slug → dev.to-friendly title)
QUEUED_POSTS = [
    {
        "slug": "codex-opencode-cline-vs-ralph-workflow-2026",
        "devto_title": "Codex CLI vs OpenCode vs Cline vs Ralph Workflow: The Unattended Coding Showdown (2026)",
        "tags": ["ai", "coding", "python", "productivity", "devops"],
        "published": False,
    },
    {
        "slug": "claude-code-automation-unattended-sessions",
        "devto_title": "How to Run Claude Code Unattended: A Practical Guide for Overnight Sessions",
        "tags": ["ai", "claude", "automation", "coding", "productivity"],
        "published": False,
    },
    {
        "slug": "spec-driven-ai-agents-why-workflow-is-the-unit-of-work",
        "devto_title": "Spec-Driven AI Agents: Why the Workflow (Not the Prompt) Is Your Real Unit of Work",
        "tags": ["ai", "softwareengineering", "architecture", "productivity"],
        "published": False,
    },
    {
        "slug": "your-first-overnight-task-start-here-guide",
        "devto_title": "Your First Overnight AI Coding Task: A Start-Here Guide",
        "tags": ["beginners", "ai", "coding", "tutorial", "productivity"],
        "published": False,
    },
    {
        "slug": "real-task-walkthrough-overnight-refactoring",
        "devto_title": "Overnight Refactoring with AI: A Real Task Walkthrough",
        "tags": ["ai", "refactoring", "python", "tutorial", "productivity"],
        "published": False,
    },
    {
        "slug": "ralph-workflow-vs-aider",
        "devto_title": "Ralph Workflow vs Aider: Terminal Pair Programming vs Composable Workflow",
        "tags": ["ai", "coding", "python", "productivity", "discuss"],
        "published": False,
    },
    {
        "slug": "ralph-workflow-vs-claude-code",
        "devto_title": "Ralph Workflow vs Claude Code: Beyond the Chat Interface",
        "tags": ["ai", "claude", "coding", "automation", "productivity"],
        "published": False,
    },
]

COOLDOWN_HOURS = 8  # Don't post more than 1 article per 8h


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"published": {}, "last_published_at": None}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def is_cooled_down(state: dict) -> bool:
    """Return True if within the cooldown window."""
    last = state.get("last_published_at")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return False
    elapsed = (datetime.now(timezone.utc) - last_dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
    return elapsed < COOLDOWN_HOURS


def read_blog_content(slug: str) -> str | None:
    """Extract markdown body from a blog post file."""
    md_path = BLOG_CONTENT_DIR / f"{slug}.md"
    if not md_path.exists():
        return None
    text = md_path.read_text(encoding="utf-8")
    # Strip frontmatter (between --- fences)
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2].strip()
    return text


def post_to_devto(api_key: str, title: str, body_md: str, tags: list[str],
                  canonical_url: str | None = None, dry_run: bool = False) -> dict:
    """Create a Dev.to article via the Forem API."""
    payload = {
        "article": {
            "title": title,
            "body_markdown": body_md,
            "tags": tags or [],
            "published": False,  # draft by default; set True for public
        }
    }
    if canonical_url:
        payload["article"]["canonical_url"] = canonical_url
    if DEVTO_ORG:
        payload["article"]["organization_id"] = DEVTO_ORG

    if dry_run:
        return {"id": 0, "url": f"https://dev.to/draft/{title[:50].lower().replace(' ', '-')}",
                "dry_run": True, "canonical_url": canonical_url, "tags": tags}

    req = urllib.request.Request(
        f"{DEVTO_API_BASE}/articles",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
            "User-Agent": "RalphWorkflow-DevTo-Crossposter/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"error": exc.code, "detail": body[:500]}


def check_queue(dry_run: bool = False) -> dict:
    """Check which posts are ready for cross-posting."""
    api_key = os.environ.get("DEVTO_API_KEY", "").strip()
    if not api_key:
        return {"status": "blocked", "reason": "DEVTO_API_KEY not set",
                "queued": len(QUEUED_POSTS),
                "published": 0, "available": 0}

    state = load_state()
    available = []
    published_slugs = set(state.get("published", {}).keys())

    for post in QUEUED_POSTS:
        slug = post["slug"]
        if slug in published_slugs:
            continue
        content = read_blog_content(slug)
        if not content:
            continue
        available.append({"slug": slug, "title": post["devto_title"],
                         "tags": post["tags"], "content_len": len(content)})

    result = {
        "status": "ok",
        "queued": len(QUEUED_POSTS),
        "published": len(published_slugs),
        "available": len(available),
        "cooldown": is_cooled_down(state),
        "posts": available,
        "api_key_set": bool(api_key),
    }
    if dry_run:
        result["dry_run"] = True
    return result


def run_post(slug: str, dry_run: bool = False) -> dict:
    """Cross-post a specific blog article to Dev.to."""
    api_key = os.environ.get("DEVTO_API_KEY", "").strip()
    if not api_key:
        return {"status": "blocked", "reason": "DEVTO_API_KEY not set"}

    state = load_state()
    if is_cooled_down(state) and not dry_run:
        last = state.get("last_published_at", "unknown")
        return {"status": "cooldown",
                "reason": f"Last published at {last}, cooldown {COOLDOWN_HOURS}h",
                "next_available": f"{COOLDOWN_HOURS}h after last post"}

    # Find matching post
    post_info = None
    for post in QUEUED_POSTS:
        if post["slug"] == slug:
            post_info = post
            break
    if not post_info:
        return {"status": "error", "reason": f"Post '{slug}' not in queue"}

    content = read_blog_content(slug)
    if not content:
        return {"status": "error", "reason": f"Blog content not found: {slug}.md"}

    canonical = f"{SITE_DOMAIN}/blog/{slug}"
    result = post_to_devto(api_key, post_info["devto_title"], content,
                           post_info["tags"], canonical_url=canonical,
                           dry_run=dry_run)

    if not dry_run and "error" not in result:
        state.setdefault("published", {})[slug] = {
            "devto_id": result.get("id"),
            "devto_url": result.get("url"),
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        state["last_published_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

    result.setdefault("canonical", canonical)
    result.setdefault("slug", slug)
    return result


if __name__ == "__main__":
    # ── Spidering guard: dev.to is permanently blocked (reCAPTCHA) ──
    try:
        from agents.marketing.channel_spidering_guard import guard_check, guard_record
        allowed, reason, remaining = guard_check("dev.to")
        if not allowed:
            print(json.dumps({"status": "spidering_blocked", "reason": f"channel_spidering_guard: {reason}", "live_external_action": False}))
            guard_record("dev.to", ok=False, fingerprint="spidering_guard_rejected")
            sys.exit(1)
    except ImportError:
        pass

    # Use COOLDOWN_HOURS from module scope (no global needed, this IS module level)
    parser = argparse.ArgumentParser(description="RalphWorkflow Dev.to crossposter")
    parser.add_argument("--dry-run", action="store_true", help="Simulate, don't actually post")
    parser.add_argument("--post", type=str, help="Post a specific blog slug")
    parser.add_argument("--mode", choices=["check-queue", "post-next"], default="check-queue",
                        help="check-queue: list available posts; post-next: post the first available")
    _default_cooldown = COOLDOWN_HOURS
    parser.add_argument("--cooldown-hours", type=float, default=None,
                        help=f"Override cooldown hours (default {_default_cooldown}h)")
    args = parser.parse_args()

    if args.cooldown_hours is not None:
        COOLDOWN_HOURS = args.cooldown_hours

    if args.post:
        result = run_post(args.post, dry_run=args.dry_run)
    elif args.mode == "check-queue":
        result = check_queue(dry_run=args.dry_run)
    elif args.mode == "post-next":
        queue = check_queue(dry_run=False)
        available = queue.get("posts", [])
        if not available:
            result = {"status": "no_posts", "reason": "All queued posts already published",
                      "queue": queue}
        else:
            next_slug = available[0]["slug"]
            result = run_post(next_slug, dry_run=args.dry_run)
    else:
        result = check_queue(dry_run=args.dry_run)

    print(json.dumps(result, indent=2, default=str))

    # Write state
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log = {"timestamp": ts, "mode": args.mode, "result": result}
    (QUEUE_PATH.parent / f"devto_crosspost_{ts}.json").write_text(json.dumps(log, indent=2))
