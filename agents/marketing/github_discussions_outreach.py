#!/usr/bin/env python3
"""GitHub Discussions Outreach Lane — search for relevant issues/discussions.

This is the NEW distribution lane mandated by the structural ceiling rule:
HN/Lobsters has been blocked for 7+ consecutive audits (3+ triggers the rule).

Unlike Reddit/Dev.to/HN, GitHub Discussions on open-source tool repos are:
- Real developers making real tooling decisions
- Not anti-bot-gated (searches are read-only from this runtime)
- Contextually relevant (people discussing AI coding tooling)
- High-intent (discussing switching tools, comparing, evaluating)

Target repos (Aider, Claude Code, Cline, Continue, Codex CLI):
- These repos have developers actively evaluating AI coding tooling
- A helpful, non-spammy reply contextualizing Ralph Workflow's approach
  can reach exactly the right audience at exactly the right moment

Usage: python3 agents/marketing/github_discussions_outreach.py [--dry-run|--status]

Design:
- Searches GitHub issues/discussions for keywords matching intent signals
- Scores > captures screener > drafts reply > logs for manual review + post
- Posts are human-executed (not automated) to avoid spam flags
- One new search+score per day max; draft bank at 5 then stop
"""

from __future__ import annotations

import json, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
STATE_PATH = ROOT / 'agents/marketing/logs/github_discussions_outreach_state.json'
DRAFTS_DIR = ROOT / 'drafts/github_discussions'
LOG_DIR = ROOT / 'agents/marketing/logs'

GITHUB_API = 'https://api.github.com'

# Repos to search — developers evaluating AI coding tooling
TARGET_REPOS = [
    'Aider-AI/aider',
    'anthropics/claude-code',
    'cline/cline',
    'continuedev/continue',
    'openai/codex',
    'microsoft/vscode',
]

# Discussion intent signals — if a post contains these, it's high-value
INTENT_KEYWORDS = [
    # Single-word and hyphenated — no URL encoding needed
    'orchestrator',
    'orchestrat',
    'multi-agent',
    'multi-model',
    'unattended',
    'spec-driven',
    'comparing',
    'comparison',
    'alternative',
    'workflow',
    'pipeline',
    'review+automation',
    'phase+routing',
    'model+routing',
    'agent+loop',
    'agent+stuck',
    'autonomous',
    'verify+output',
    'cost+arbitrage',
    'switch+from',
]

# Max drafts in the bank before stopping
MAX_DRAFT_BANK = 5
# Only search once per 24h
SEARCH_COOLDOWN_HOURS = 24


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {'drafts': [], 'searches': [], 'total_replies_posted': 0}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str) + '\n', encoding='utf-8')


def search_github_issues(repo: str, keyword: str, max_results: int = 10) -> list[dict]:
    """Search GitHub issues for a keyword in a specific repo.

    Uses GitHub's REST API (no auth needed for public repos, but rate-limited).
    """
    encoded_kw = urllib.parse.quote(keyword)
    url = f'{GITHUB_API}/search/issues?q={encoded_kw}+repo:{repo}+type:issue+state:open&sort=created&order=desc&per_page={max_results}'
    
    req = urllib.request.Request(url)
    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('User-Agent', 'RalphWorkflow/1.0 (marketing-outreach)')
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            return data.get('items', [])
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(f'  Rate limited for {repo}/{keyword}')
            return []
        print(f'  HTTP {e.code} for {repo}/{keyword}')
        return []
    except Exception as e:
        print(f'  Error: {e}')
        return []


def score_issue(issue: dict) -> float:
    """Score an issue for outreach relevance. Higher = better target."""
    score = 0.0
    title = (issue.get('title') or '').lower()
    body = (issue.get('body') or '').lower()
    text = title + ' ' + body
    
    comments = issue.get('comments', 0)
    
    # Active discussions are better
    if comments >= 3:
        score += 1.0
    if comments >= 5:
        score += 0.5
    if comments >= 10:
        score += 0.5
    
    # Recent is better
    created = issue.get('created_at', '')
    if created:
        try:
            dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            age_days = (datetime.now(timezone.utc) - dt).days
            if age_days <= 7:
                score += 2.0
            elif age_days <= 30:
                score += 1.0
            elif age_days <= 90:
                score += 0.5
        except (ValueError, TypeError):
            pass
    
    # Keyword match strength
    for kw in INTENT_KEYWORDS:
        if kw.lower() in text:
            score += 0.5
    
    # Title match is stronger
    for kw in INTENT_KEYWORDS:
        if kw.lower() in title:
            score += 0.5
    
    # "vs" or "or" in title = comparison intent
    if ' vs ' in title or 'vs.' in title:
        score += 1.0
    
    # If explicitly asking about alternatives
    alternative_signals = ['alternative', 'instead of', 'replace', 'switch']
    for sig in alternative_signals:
        if sig in text:
            score += 1.0
            break  # only count once
    
    return score


def generate_reply_draft(issue: dict, repo: str, score: float) -> dict:
    """Generate a helpful, non-spammy reply draft.
    
    The draft is for HUMAN REVIEW + POSTING. We never auto-post to GitHub.
    """
    title = issue.get('title', 'Untitled')
    issue_number = issue.get('number', 0)
    issue_url = issue.get('html_url', '')
    repo_name = repo.split('/')[-1]
    
    # Build a context-appropriate response
    # Different angles depending on what the issue is about
    
    reply = f"""## Reply Draft for: {issue_url}

**Issue:** {title}
**Repo:** {repo_name}
**Score:** {score:.1f} (relevance)
**Status:** ⬜ NEEDS HUMAN REVIEW — DO NOT AUTO-POST

---

[Review the full thread at {issue_url} before posting. Adjust tone and content to match the specific discussion.]

---

I ran into similar challenges and ended up building a composable workflow orchestrator that lets you route different phases (planning, development, review, fix) to different agents (Claude Code, Codex CLI, OpenCode, etc.) via a TOML config file.

The key insight was that a single-agent loop misses too many edge cases, but manually switching between tools is even worse. The orchestrator handles the handoffs with clear phase gates and artifact-based verification.

It's free and open-source — [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow) primary, [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow). The TOML config makes it trivial to experiment with different agent/model combinations.

*This is a manual outreach draft generated by the Ralph Workflow marketing system. Human review required before posting.*
"""

    return {
        'issue_url': issue_url,
        'issue_title': title,
        'issue_number': issue_number,
        'repo': repo,
        'score': score,
        'reply_draft': reply,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'needs_review',
    }


def can_search_now(state: dict) -> tuple[bool, str]:
    """Check if we're allowed to run a new search."""
    searches = state.get('searches', [])
    if not searches:
        return True, 'first_search'
    
    last_search = max(
        (s.get('timestamp', '') for s in searches),
        default='',
    )
    if last_search:
        try:
            last_dt = datetime.fromisoformat(last_search.replace('Z', '+00:00'))
            elapsed = datetime.now(timezone.utc) - last_dt
            if elapsed < timedelta(hours=SEARCH_COOLDOWN_HOURS):
                remaining = SEARCH_COOLDOWN_HOURS - elapsed.total_seconds() / 3600
                return False, f'cooldown ({remaining:.1f}h remaining)'
        except (ValueError, TypeError):
            pass
    
    return True, 'ok'


def main() -> int:
    # ── Spidering guard: GitHub Discussions search cooldown ──
    try:
        from agents.marketing.channel_spidering_guard import guard_check, guard_record
        allowed, reason, remaining = guard_check("github-discussions")
        if not allowed:
            print(f"[GitHub Discussions] BLOCKED by spidering guard: {reason} ({remaining:.1f}h remaining)")
            guard_record("github-discussions", ok=False, fingerprint="spidering_guard_rejected")
            return 1
    except ImportError:
        pass

    if '--status' in sys.argv:
        state = load_state()
        can, reason = can_search_now(state)
        drafts = state.get('drafts', [])
        unreviewed = [d for d in drafts if d.get('status') == 'needs_review']
        posted = state.get('total_replies_posted', 0)
        print('=== GitHub Discussions Outreach Lane ===')
        print(f'Drafts in bank: {len(drafts)}/{MAX_DRAFT_BANK}')
        print(f'Unreviewed: {len(unreviewed)}')
        print(f'Total posted: {posted}')
        print(f'Can search: {can} ({reason})')
        if unreviewed:
            print('\nUnreviewed drafts:')
            for d in unreviewed[:5]:
                print(f'  [{d.get("score", 0):.1f}] {d.get("repo", "")} #{d.get("issue_number", "")}: {d.get("issue_title", "")[:80]}')
        return 0

    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print('[DRY RUN] GitHub Discussions Outreach Lane')

    state = load_state()
    drafts = state.get('drafts', [])

    # Bank is full — stop generating
    if len(drafts) >= MAX_DRAFT_BANK:
        unreviewed = [d for d in drafts if d.get('status') == 'needs_review']
        if unreviewed:
            print(f'SKIP: Draft bank full ({len(drafts)}/{MAX_DRAFT_BANK}). {len(unreviewed)} need human review.')
        else:
            print(f'SKIP: Draft bank full ({len(drafts)}/{MAX_DRAFT_BANK}). All reviewed. Post or clear to generate more.')
        return 0

    # Cooldown check
    can, reason = can_search_now(state)
    if not can and '--force' not in sys.argv:
        print(f'SKIP: {reason}')
        return 0

    # Search across repos and keywords
    print(f'Searching {len(TARGET_REPOS)} repos for intent signals...')
    all_issues = []
    seen_urls = set()

    for repo in TARGET_REPOS:
        for kw in INTENT_KEYWORDS[:6]:  # Limit keywords per repo to avoid rate limits
            issues = search_github_issues(repo, kw, max_results=5)
            for issue in issues:
                url = issue.get('html_url', '')
                if url not in seen_urls:
                    seen_urls.add(url)
                    score = score_issue(issue)
                    if score >= 2.0:  # Minimum relevance threshold
                        all_issues.append((issue, repo, score))
            time.sleep(1.5)  # Rate limit respect (unauthenticated: 60/hr)

    if not all_issues:
        print('No high-scoring issues found.')
        state.setdefault('searches', []).append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'results': 0,
            'dry_run': dry_run,
        })
        save_state(state)
        return 0

    # Sort by score and take top candidates
    all_issues.sort(key=lambda x: x[2], reverse=True)
    new_drafts_count = 0

    for issue, repo, score in all_issues:
        if len(drafts) >= MAX_DRAFT_BANK:
            break
        
        # Skip if we already have a draft for this issue
        existing_urls = {d.get('issue_url', '') for d in drafts}
        if issue.get('html_url', '') in existing_urls:
            continue

        if not dry_run:
            draft = generate_reply_draft(issue, repo, score)
            # Save individual draft
            draft_path = DRAFTS_DIR / f"{repo.replace('/', '_')}_{issue.get('number', 0)}.md"
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(draft['reply_draft'], encoding='utf-8')
            draft['draft_path'] = str(draft_path)
            drafts.append(draft)
            new_drafts_count += 1

    # Update state
    state['drafts'] = drafts
    state.setdefault('searches', []).append({
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'results': len(all_issues),
        'new_drafts': new_drafts_count,
        'dry_run': dry_run,
    })
    save_state(state)

    print(f'Found: {len(all_issues)} high-scoring issues')
    print(f'New drafts: {new_drafts_count}')
    print(f'Draft bank: {len(drafts)}/{MAX_DRAFT_BANK}')

    # Log
    log_entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'action_type': 'github_discussions_outreach_search',
        'dry_run': dry_run,
        'issues_found': len(all_issues),
        'new_drafts': new_drafts_count,
        'ok': True,
        'live_external_action': False,  # Human posts, not automated
    }
    log_path = LOG_DIR / f'marketing_{datetime.now().strftime("%Y-%m-%d_%H%M%S")}_github_discussions.json'
    log_path.write_text(json.dumps(log_entry, indent=2, default=str) + '\n', encoding='utf-8')
    print(f'Log: {log_path}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
