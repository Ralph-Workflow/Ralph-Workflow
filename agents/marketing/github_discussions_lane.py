#!/usr/bin/env python3
"""GitHub Discussions lane bootstrap — replaces dead Reddit/HN/Lobsters distribution.

This lane targets the GitHub Discussions feature on the mirror repo as a
genuinely unblocked alternative to Reddit for developer community growth.

Pre-requisites (browser-based, one-time):
  1. `gh auth login` — authenticate GitHub CLI
  2. Enable Discussions on the repo: GitHub Repo → Settings → Features → Discussions

Once enabled, this lane can post seed discussions and monitor engagement
autonomously from this runtime.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

MIRROR_REPO = 'Ralph-Workflow/Ralph-Workflow'
CODEBERG_REPO = 'https://codeberg.org/RalphWorkflow/Ralph-Workflow'

# Seed discussion topics — each addresses a real developer pain point that
# existing blog content can reference
SEED_DISCUSSIONS: list[dict[str, Any]] = [
    {
        "title": "What's your AI coding workflow? (vs. chat sessions)",
        "body": (
            "Curious how teams are structuring their AI-assisted development "
            "workflows. Are you still using a chat-session model (paste prompt, "
            "copy code, repeat), or have you moved to something more structured?\n\n"
            "We built [Ralph Workflow](" + CODEBERG_REPO + ") as an open-source "
            "orchestrator that runs a planning→development loop. But I'd love to "
            "hear what's working for other teams — especially around verification "
            "and testing phases."
        ),
        "label": "💬 Discussion",
    },
    {
        "title": "How do you handle agent context limits in long coding sessions?",
        "body": (
            "One of the recurring problems we hit with AI coding agents was context "
            "window exhaustion during long sessions. The agent would start strong, "
            "then forget earlier decisions halfway through.\n\n"
            "Our approach in [Ralph Workflow](" + CODEBERG_REPO + ") uses checkpoint "
            "files + phase-based summaries. What patterns have you found effective?"
        ),
        "label": "💬 Discussion",
    },
    {
        "title": "Show & Tell: Share your agent orchestration setup",
        "body": (
            "If you're orchestrating multiple AI agents for development, share your "
            "setup! Interested in: pipeline structure, prompt management, verification "
            "strategies, and how you handle agent-to-agent handoff.\n\n"
            "We'll share ours too — [Ralph Workflow](" + CODEBERG_REPO + ") is a "
            "free open-source loop framework for this exact problem."
        ),
        "label": "💬 Discussion",
    },
]


def check_gh_authenticated() -> tuple[bool, str]:
    """Verify gh CLI is authenticated."""
    try:
        result = subprocess.run(
            ['gh', 'auth', 'status'],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0, result.stdout.strip()
    except FileNotFoundError:
        return False, 'gh CLI not installed — run `gh auth login` first'
    except subprocess.TimeoutExpired:
        return False, 'gh auth status timed out'


def check_discussions_enabled() -> tuple[bool, str]:
    """Check if Discussions are enabled on the mirror repo."""
    try:
        # List discussion categories — fails if Discussions not enabled
        result = subprocess.run(
            ['gh', 'api', f'repos/{MIRROR_REPO}/discussions/categories'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return False, (
                f'Discussions not enabled on {MIRROR_REPO}. '
                'Enable at: https://github.com/{MIRROR_REPO}/settings. '
                f'Raw error: {result.stderr.strip()[:200]}'
            )
        categories = json.loads(result.stdout)
        names = [c.get('name', '?') for c in categories] if isinstance(categories, list) else []
        return True, f'Enabled — categories: {names}'
    except FileNotFoundError:
        return False, 'gh CLI not installed'
    except Exception as e:
        return False, str(e)


def post_discussion(title: str, body: str, category_label: str | None = None) -> tuple[bool, str]:
    """Post a seed discussion to GitHub Discussions."""
    # Get category by label
    category_id = None
    if category_label:
        try:
            result = subprocess.run(
                ['gh', 'api', f'repos/{MIRROR_REPO}/discussions/categories'],
                capture_output=True, text=True, timeout=15,
            )
            categories = json.loads(result.stdout)
            for cat in categories:
                if isinstance(cat, dict) and cat.get('name') == category_label:
                    category_id = cat['id']
                    break
        except Exception:
            pass

    cmd = [
        'gh', 'api', f'repos/{MIRROR_REPO}/discussions',
        '-f', f'title={title}',
        '-f', f'body={body}',
    ]
    if category_id is not None:
        cmd.extend(['-f', f'categoryId={category_id}'])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return False, result.stderr.strip()[:500]
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        url = data.get('html_url', 'unknown')
        return True, url
    except Exception as e:
        return False, str(e)


def generate_report() -> dict[str, Any]:
    """Generate a lane readiness report."""
    now = datetime.now().astimezone()

    gh_ok, gh_msg = check_gh_authenticated()
    disc_ok, disc_msg = check_discussions_enabled()

    return {
        'generated_at': now.isoformat(),
        'lane': 'github_discussions',
        'status': 'ready' if (gh_ok and disc_ok) else 'needs_setup',
        'gh_authenticated': gh_ok,
        'gh_auth_message': gh_msg,
        'discussions_enabled': disc_ok,
        'discussions_message': disc_msg,
        'seed_topics_count': len(SEED_DISCUSSIONS),
        'next_steps': ([] if (gh_ok and disc_ok) else [
            'Run `gh auth login` in a terminal (requires browser)',
            f'Enable Discussions: https://github.com/{MIRROR_REPO}/settings',
        ]),
        'action': 'post_seed_discussions' if (gh_ok and disc_ok) else 'setup_required',
    }


def main() -> int:
    # ── Spidering guard: GitHub Discussions search cooldown ──
    try:
        from agents.marketing.channel_spidering_guard import guard_check
        allowed, reason, remaining = guard_check("github-discussions-search")
        if not allowed:
            print(f"[GitHub Discussions Lane] BLOCKED: {reason} ({remaining:.1f}h remaining)")
            return 1
    except ImportError:
        pass

    report = generate_report()
    report_path = LOG_DIR / 'github_discussions_lane_latest.json'
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')

    print(json.dumps(report, indent=2))

    if report['status'] == 'needs_setup':
        print('\n⚠️  GitHub Discussions lane needs one-time browser setup:')
        for step in report['next_steps']:
            print(f'  → {step}')
        return 1

    print('\n✅ GitHub Discussions lane is ready for posting')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
