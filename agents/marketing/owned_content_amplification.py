#!/usr/bin/env python3
"""Owned Content Amplification Lane — publish blog posts to Ralph-Site.

This is the ONLY distribution channel that works reliably from this runtime IP.
All outbound channels (Reddit, Dev.to, HN, Apollo, Mastodon, ProductHunt) are
anti-bot-blocked. Ralph-Site blog posts are the primary mechanism for:

1. SEO traffic (search → article → Codeberg CTA)
2. Long-tail comparison keywords (Codex CLI vs ..., OpenCode vs ...)
3. Authority building (more indexed pages = more discoverability)

Usage: python3 agents/marketing/owned_content_amplification.py [--dry-run|--status]

Design principles:
- Target uncovered keyword gaps (don't duplicate existing posts)
- Every post has a primary Codeberg CTA + GitHub mirror CTA
- Publish to Ralph-Site git repo → CI/CD deploys automatically
- Minimize model cost: use the smallest capable model for content generation
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
SITE_REPO = ROOT / 'Ralph-Site'
CONTENT_DIR = SITE_REPO / 'content' / 'guides'
BLOG_DIR = SITE_REPO / 'content' / 'blog'
LANE_STATE_PATH = ROOT / 'agents/marketing/logs/owned_content_amplification_state.json'
MARKET_INTEL_PATH = ROOT / 'agents/marketing/logs/market_intelligence_latest.json'
SEO_REPORTS_DIR = ROOT / 'seo-reports'

# Avoid repeating the same keyword cluster within 24h
POST_COOLDOWN_HOURS = 12

# Keyword clusters we've already published (prevent duplicates)
# Synced 2026-05-30: 37 blog posts now published — all keyword clusters covered.
# New additions should go into UNCOVERED_KEYWORD_CLUSTERS below.
PUBLISHED_KEYWORD_CLUSTERS = {
    'unattended-ai-coding',                    # home page + comparison matrix
    'ai-coding-agent-comparison',              # comparison matrix page
    'codex-cli-vs-opencode',                   # May 28 comparison article
    'ai-agent-verification',                   # May 28 verification article
    'hello-ralph-workflow',                    # intro post
    'how-to-run-claude-code-unattended',        # Claude Code guide
    'spec-driven-ai-agents',                   # workflow-centric guide
    'autonomous-ai-workflows-reliability',      # production reliability
    'review-ai-coding-output',                 # review guide
    'multi-agent-orchestration-patterns',       # May 29 multi-agent orchestration
    'ai-coding-cost-optimization',              # AI Cost Model Routing published
    'ai-agent-tool-calling-debugging',          # When Your AI Coding Agent Gets Stuck
    'offline-ai-coding',                        # Can You Actually Run AI Coding Agents Offline?
    'ai-code-review-automation',               # AI Agent Output Verification
    'ai-coding-ci-cd-integration',              # NEW: CI/CD pipeline integration
    'ai-agent-testing-patterns',                # NEW: testing patterns for AI-generated code
    'prompt-engineering-coding-agents',         # NEW: prompt engineering for coding agents
    'model-routing-optimization',               # cost arbitrage + model routing
    'spec-driven-ai-agent-explicit-contracts',  # why explicit contracts matter
    'nested-analysis-loops',                    # How Nested Analysis Loops Catch Bugs
    'claude-code-automation',                   # Claude Code Automation
    'claude-code-autonomous-mode-wrapper',     # Claude Code Autonomous Mode Wrapper
    'overnight-refactoring-walkthrough',        # Overnight Refactoring with Ralph Workflow
    'good-vs-bad-unattended-tasks',             # Good vs Bad Unattended AI Coding Tasks
    'safe-ai-code-execution',                   # Safe AI Code Execution
    'first-overnight-task-guide',               # Your First Overnight Task Start-Here Guide
    'autonomous-coding-compared',               # Autonomous AI Coding Tools Compared
    'ai-coding-tools-compared-2026',            # AI Coding Tools Compared 2026
    'is-ralph-right-for-you',                   # Decision Guide
    'quickstart-getting-started',              # Ralph Workflow in 5 Minutes
    'ai-coding-agent-testing-strategy',           # NEW: testing strategy for AI-generated code
    'ralph-workflow-vs-aider',                  # comparison
    'ralph-workflow-vs-claude-code',            # comparison
    'ralph-workflow-vs-conductor-oss',          # comparison
    'ralph-workflow-vs-conductor-teams',        # comparison
    'ralph-workflow-vs-continue',               # comparison
    'ralph-workflow-vs-cursor',                 # comparison
    'ralph-workflow-vs-github-copilot',         # comparison
    'ralph-workflow-vs-hermes-agent',           # comparison
    'ai-agent-orchestration-category',          # The AI Agent Orchestration Category
    'debugging-failed-overnight-run',           # When Your Overnight AI Coding Run Fails
}

# NEW untapped keyword clusters (refreshed 2026-05-30 after 37-pub saturation scan)
# These are genuinely uncovered topics after reviewing all 37 published posts.
UNCOVERED_KEYWORD_CLUSTERS = [
    {
        'cluster': 'ci-cd-pipeline-ai-coding-agent',
        'title': 'CI/CD Pipeline for AI Coding Agents: Running Autonomous Code Generation in Your Build System',
        'target_keywords': [
            'CI/CD AI coding', 'AI agent CI pipeline', 'autonomous coding CI',
            'run AI code generation in CI', 'GitHub Actions AI coding',
            'AI coding agent build system', 'continuous integration AI code generation',
        ],
        'angle': (
            'How to wire an AI coding agent into your existing CI/CD pipeline — running '
            'autonomous code generation as part of your build, with gates, rollback, and '
            'human-in-the-loop approval. GitHub Actions, GitLab CI, and generic patterns '
            'that work across all build systems.'
        ),
        'codeberg_cta_angle': 'Ralph Workflow runs as a CLI tool — plug it directly into any CI/CD pipeline via ralph run.',
    },
    {
        'cluster': 'ai-coding-agent-benchmarks-real-world',
        'title': 'AI Coding Agent Benchmarks That Actually Matter: Beyond SWE-bench to Real-World Performance',
        'target_keywords': [
            'AI coding agent benchmarks', 'SWE-bench vs real-world', 'coding agent performance',
            'AI coding accuracy real projects', 'compare AI coding agents real world',
            'coding agent benchmark real tasks',
        ],
        'angle': (
            'SWE-bench scores are a starting point, not the whole story. This article '
            'compares AI coding agents on real-world metrics: completion rate on non-trivial '
            'tasks, review-after cost, iteration count before merge-ready, and failure mode '
            'diversity. No vendor benchmarks — just observed results from real runs.'
        ),
        'codeberg_cta_angle': 'Ralph Workflow is vendor-neutral — benchmark any agent with the same pipeline for apples-to-apples comparison.',
    },
]


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False) + '\n', encoding='utf-8')


def get_existing_post_slugs() -> set[str]:
    """Collect slugs from all existing content to prevent duplicates."""
    slugs = set()
    for directory in (CONTENT_DIR, BLOG_DIR):
        if directory.exists():
            for md_file in directory.glob('*.md'):
                text = md_file.read_text(encoding='utf-8')
                # Extract slug from frontmatter if present
                for line in text.split('\n')[:20]:
                    if line.startswith('slug:') or line.startswith('permalink:'):
                        slug = line.split(':', 1)[1].strip().strip('"\'')
                        slugs.add(slug)
                # Also use filename as fallback slug
                slugs.add(md_file.stem.lower().replace('_', '-').replace(' ', '-'))
    return slugs


def find_next_uncovered_cluster(existing_slugs: set[str]) -> dict | None:
    """Find the first keyword cluster not covered by existing content."""
    for cluster in UNCOVERED_KEYWORD_CLUSTERS:
        slug = cluster['cluster']
        if slug not in existing_slugs and slug not in PUBLISHED_KEYWORD_CLUSTERS:
            title_words = set(cluster['title'].lower().split())
            # Check if any existing slug overlaps significantly
            overlap = False
            for es in existing_slugs:
                es_words = set(es.replace('-', ' ').split())
                if len(title_words & es_words) >= 3:
                    overlap = True
                    break
            if not overlap:
                return cluster
    return None


CONTENT_SATURATION_THRESHOLD = 40  # 2026-06-01: at 44 live posts, each new post has near-zero SEO value (13/44 indexed)


def _live_post_count() -> int:
    """Count live blog posts from sitemap; fall back to local content directory."""
    try:
        import re
        req = urllib.request.Request(
            "https://ralphworkflow.com/sitemap.xml",
            headers={"User-Agent": "RalphWorkflow-ContentSatGuard/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
        blog_urls = re.findall(r'https://ralphworkflow\.com/blog/[^<]+', body)
        count = len([u for u in blog_urls if 'feed.json' not in u])
        if count > 0:
            return count
    except Exception:
        pass
    blog_dir = ROOT / 'Ralph-Site' / 'content' / 'blog'
    if blog_dir.is_dir():
        return len(list(blog_dir.glob('*.md')))
    return 44


def can_publish_now() -> tuple[bool, str]:
    """Check if we're allowed to publish now (cooldown, saturation, etc.)."""
    # Content saturation gate: when ≥THRESHOLD posts, new posts have vanishing SEO value.
    # Redirect effort to retrofitting existing posts instead.
    live_count = _live_post_count()
    if live_count >= CONTENT_SATURATION_THRESHOLD:
        return False, f'content saturation ({live_count} live posts >= {CONTENT_SATURATION_THRESHOLD} threshold; redirect to SEO retrofit of existing posts)'
    state = load_json(LANE_STATE_PATH)
    last_pub = state.get('last_published_at')
    if last_pub:
        last_dt = datetime.fromisoformat(last_pub.replace('Z', '+00:00'))
        elapsed = datetime.now(timezone.utc) - last_dt
        if elapsed < timedelta(hours=POST_COOLDOWN_HOURS):
            remaining = POST_COOLDOWN_HOURS - elapsed.total_seconds() / 3600
            return False, f'cooldown ({remaining:.1f}h remaining)'
    return True, 'ok'


def generate_blog_post(cluster: dict, dry_run: bool = False) -> dict:
    """Generate a blog post targeting a keyword cluster.

    Uses the OpenClaw content tooling to create a properly formatted post
    with Codeberg CTAs embedded.
    """
    if dry_run:
        return {'ok': True, 'dry_run': True, 'title': cluster['title']}

    slug = cluster['cluster']
    title = cluster['title']
    
    # Use cluster-specific angle as the body; add a Ralph Workflow CTA footer.
    # The angle field carries the full editorial direction from the content strategy.
    cta = cluster.get('codeberg_cta_angle', 'Ralph Workflow runs as a CLI tool — plug it directly into any CI/CD pipeline via ralph run.')
    keywords_csv = ', '.join(cluster.get('target_keywords', ['AI coding agent', 'autonomous coding'])[:5])

    content_md = f"""---
title: "{title}"
published_on: "{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
tags:
  - ai
  - coding
  - automation
  - open-source
  - workflow
description: "{cluster['angle'][:150]}"
---

# {title}

{cluster['angle']}

## How Ralph Workflow addresses this

[Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) is a free
and open-source orchestrator that turns AI coding agents into unattended
workflows with built-in verification, fix loops, and review gates.

{cta}

## Getting started

```bash
pip install ralph-workflow
ralph init my-project
ralph run --task "Build a REST API for user authentication"
```

**Primary repo (Codeberg):** [codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
**Mirror (GitHub):** [github.com/Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow)
**Docs:** [ralphworkflow.com/docs](https://ralphworkflow.com/docs)

---

*Keywords: {keywords_csv}.*
"""
    
    output_path = BLOG_DIR / f"{slug}.md"
    
    if dry_run:
        return {'ok': True, 'dry_run': True, 'path': str(output_path), 'title': title}
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content_md, encoding='utf-8')
    
    return {
        'ok': True,
        'path': str(output_path),
        'title': title,
        'slug': slug,
        'keywords': cluster['target_keywords'],
    }


def deploy_to_ralph_site(dry_run: bool = False) -> dict:
    """Commit and push blog post to Ralph-Site repo, then deploy."""
    if dry_run:
        return {'ok': True, 'dry_run': True, 'step': 'deploy'}

    result = {'ok': False, 'step': 'start'}
    
    try:
        # Check if git repo is accessible
        git_status = subprocess.run(
            ['git', '-C', str(SITE_REPO), 'status', '--porcelain'],
            capture_output=True, text=True, timeout=30,
        )
        
        if not git_status.stdout.strip():
            return {'ok': True, 'step': 'no_changes', 'message': 'nothing to commit'}

        # Commit
        commit_msg = f'feat(seo): new blog post from owned-content-amplification lane'
        subprocess.run(
            ['git', '-C', str(SITE_REPO), 'add', 'content/'],
            capture_output=True, text=True, timeout=30, check=True,
        )
        subprocess.run(
            ['git', '-C', str(SITE_REPO), 'commit', '-m', commit_msg],
            capture_output=True, text=True, timeout=30, check=True,
        )
        
        # Push
        push = subprocess.run(
            ['git', '-C', str(SITE_REPO), 'push', 'origin', 'main'],
            capture_output=True, text=True, timeout=60,
        )
        
        if push.returncode == 0:
            result['ok'] = True
            result['step'] = 'pushed'
            result['message'] = f'Committed and pushed: {commit_msg}'
        else:
            result['ok'] = False
            result['step'] = 'push_failed'
            result['error'] = push.stderr[:300]
            
    except subprocess.TimeoutExpired:
        result['ok'] = False
        result['step'] = 'timeout'
        result['error'] = 'Git operation timed out'
    except subprocess.CalledProcessError as e:
        result['ok'] = False
        result['step'] = 'git_error'
        result['error'] = f'{e.stderr[:300] if e.stderr else str(e)}'
    except Exception as e:
        result['ok'] = False
        result['step'] = 'exception'
        result['error'] = str(e)[:300]
    
    return result


def main() -> int:
    if '--status' in sys.argv:
        state = load_json(LANE_STATE_PATH)
        existing = get_existing_post_slugs()
        next_cluster = find_next_uncovered_cluster(existing)
        can, reason = can_publish_now()
        print('=== Owned Content Amplification Lane ===')
        print(f'Existing slugs: {len(existing)}')
        if next_cluster:
            print(f'Next uncovered: {next_cluster["title"]}')
        else:
            print('Next uncovered: NONE')
        print(f'Can publish: {can} ({reason})')
        last_pub = state.get('last_published_at', 'never')
        print(f'Last published: {last_pub}')
        return 0

    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print('[DRY RUN] Owned Content Amplification Lane')
    
    # Check cooldown / saturation
    can, reason = can_publish_now()
    if not can and '--force' not in sys.argv:
        # If blocked by content saturation, redirect to SEO retrofit
        if 'saturation' in reason.lower():
            print(f'SKIP publish: {reason} — redirecting to SEO retrofit lane')
            from agents.marketing.seo_retrofit_lane import run as retrofit_run
            result = retrofit_run(dry_run=dry_run)
            print(f'Retrofit complete: {json.dumps(result.get("summary", result), indent=2)}')
            return 0
        print(f'SKIP: {reason}')
        return 0

    # Find uncovered cluster
    existing = get_existing_post_slugs()
    cluster = find_next_uncovered_cluster(existing)
    
    if not cluster:
        print('SKIP: All keyword clusters covered. No new post needed.')
        return 0

    print(f'Target cluster: {cluster["cluster"]}')
    print(f'Title: {cluster["title"]}')
    
    # Generate post
    post_result = generate_blog_post(cluster, dry_run=dry_run)
    
    if not post_result.get('ok'):
        print(f'FAILED: Post generation error: {post_result.get("error")}')
        return 1

    print(f'Post generated: {post_result.get("path", "N/A")}')

    # Deploy
    deploy_result = deploy_to_ralph_site(dry_run=dry_run)
    print(f'Deploy: {deploy_result.get("step")} - {deploy_result.get("message", deploy_result.get("error", ""))}')

    # Update state
    if not dry_run and deploy_result.get('ok'):
        state = load_json(LANE_STATE_PATH)
        state['last_published_at'] = datetime.now(timezone.utc).isoformat()
        state.setdefault('published_clusters', []).append({
            'cluster': cluster['cluster'],
            'title': cluster['title'],
            'published_at': datetime.now(timezone.utc).isoformat(),
            'path': post_result.get('path', ''),
        })
        save_json(state, LANE_STATE_PATH)

    # Log result
    log_entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'action_type': 'owned_content_amplification',
        'dry_run': dry_run,
        'cluster': cluster['cluster'],
        'title': cluster['title'],
        'post_result': post_result,
        'deploy_result': deploy_result,
        'ok': post_result.get('ok') and deploy_result.get('ok'),
        'live_external_action': deploy_result.get('ok') and not dry_run,
    }
    
    log_path = ROOT / 'agents/marketing/logs' / f'marketing_{datetime.now().strftime("%Y-%m-%d_%H%M%S")}_owned_content_amplification.json'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log_entry, indent=2, default=str) + '\n', encoding='utf-8')

    print(f'\nLog: {log_path}')
    return 0 if post_result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
