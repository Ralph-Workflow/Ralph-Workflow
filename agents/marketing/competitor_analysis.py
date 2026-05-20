#!/usr/bin/env python3
"""Competitor Analysis — RalphWorkflow competitor monitoring and comparison.

Runs on a daily loop. Tracks competitor websites, generates comparison content,
identifies gaps, and surfaces insights for the marketing strategy.

Competitors tracked:
- Hermes Agent (hermes-agent.org) — Nous Research, self-improving agent, persistent memory
- Conductor OSS (conductor-oss.org) — Microsoft/Azure, durable execution, workflow engine
- Aider (aider.chat) — Terminal-based AI coding assistant
- Continue (continue.dev) — VS Code / JetBrains extension, multi-model
- Cursor (cursor.com) — AI-first code editor
- Claude Code (claude.ai/code) — Anthropic's official CLI agent
- GitHub Copilot (github.com/features/copilot) — Microsoft's GitHub-integrated agent

Differentiators for Ralph Workflow:
- Multi-agent phase routing (planning/review/fix with different model families)
- Vendor-neutral (Claude + Codex + OpenCode in one pipeline)
- Cost arbitrage (cheap models where sufficient, frontier where needed)
- Policy-defined TOML configuration (not prompt engineering)
- Real unattended execution with structured artifact contracts
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing")
REPORTS_DIR = Path("/home/mistlight/.openclaw/workspace/seo-reports")
COMPETITOR_REPORTS = REPORTS_DIR / "competitors"
COMPETITOR_REPORTS.mkdir(parents=True, exist_ok=True)

SITE = "ralphworkflow.com"
SITE_URL = f"https://{SITE}"

# Full competitor registry
COMPETITORS = {
    "hermes-agent": {
        "name": "Hermes Agent",
        "url": "https://hermes-agent.org",
        "docs_url": "https://hermes-ai.net/en/docs/",
        "github": "https://github.com/nousresearch/hermes-agent",
        "key_strengths": ["Persistent memory", "Self-improving", "70+ built-in skills", "Multi-platform (15+ chat platforms)", "Every LLM provider"],
        "pricing": "Free / Open source",
        "positioning": "Self-improving agent that learns from experience",
    },
    "conductor-oss": {
        "name": "Conductor OSS",
        "url": "https://conductor-oss.org",
        "docs_url": "https://docs.conductor-oss.org/",
        "github": "https://github.com/conductor-oss/conductor",
        "key_strengths": ["Durable execution", "14+ LLM providers", "MCP support", "Vector DB built-in", "Battle-tested at Netflix scale"],
        "pricing": "Free / Open source (Apache 2.0)",
        "positioning": "Enterprise-grade workflow orchestration for AI agents",
    },
    "conductor-teams": {
        "name": "Conductor (Teams)",
        "url": "https://conductross.com",
        "docs_url": None,
        "github": None,
        "key_strengths": ["Markdown-native config", "Local-first", "Branch/worktree modes", "Parallel agent execution", "tmux integration"],
        "pricing": "Free / Open source",
        "positioning": "Markdown-native local-first orchestration for coding teams",
    },
    "aider": {
        "name": "Aider",
        "url": "https://aider.chat",
        "docs_url": "https://aider.chat/docs/",
        "github": "https://github.com/paul-gauthier/aider",
        "key_strengths": ["Terminal-based", "Git-native", "Multiple LLMs", "Edit modes", "Very popular (20k+ stars)"],
        "pricing": "Free / Open source",
        "positioning": "Terminal-based AI pair programming in your git repo",
    },
    "continue": {
        "name": "Continue",
        "url": "https://continue.dev",
        "docs_url": "https://continue.dev/docs/",
        "github": "https://github.com/continuedev/continue",
        "key_strengths": ["VS Code + JetBrains", "Multi-model", "Codebase aware", "Open source", "15k+ stars"],
        "pricing": "Free / Open source",
        "positioning": "The leading AI code assistant inside your IDE",
    },
    "cursor": {
        "name": "Cursor",
        "url": "https://cursor.com",
        "docs_url": "https://docs.cursor.com",
        "github": None,
        "key_strengths": ["AI-first editor", "Tab autocomplete", "Composer", "Context-aware", "Extremely popular"],
        "pricing": "Free tier / $20/mo Pro",
        "positioning": "The AI code editor built for pair programming with AI",
    },
    "claude-code": {
        "name": "Claude Code",
        "url": "https://claude.ai/code",
        "docs_url": "https://docs.anthropic.com/en/docs/claude-code",
        "github": None,
        "key_strengths": ["Anthropic's official CLI", "Deep Claude integration", "File editing", "Tool use", "Agentic execution"],
        "pricing": "Included with Claude subscription",
        "positioning": "Anthropic's official CLI for agentic coding",
    },
    "copilot": {
        "name": "GitHub Copilot",
        "url": "https://github.com/features/copilot",
        "docs_url": "https://docs.github.com/en/copilot",
        "github": None,
        "key_strengths": ["Deep GitHub integration", "IDE-native", "Inline suggestions", "Chat mode", "Massive user base"],
        "pricing": "$10/mo individual / $19/mo Business",
        "positioning": "Your AI pair programmer embedded in GitHub and your IDE",
    },
}

# Keywords where Ralph Workflow has advantages to exploit
# Ralph Workflow key advantages used in marketing messaging
RALPH_ADVANTAGES = [
    "Multi-agent phase routing (planning → development → review → fix)",
    "Cost arbitrage: Claude + Codex + OpenCode in the same pipeline",
    "Policy-defined orchestration via TOML configuration",
    "True unattended execution with artifact-based completion",
    "Vendor-neutral: own your config, not the tool",
]

RALPH_ADVANTAGE_KEYWORDS = [
    "unattended coding pipeline",
    "multi-agent orchestration",
    "Claude Code workflow",
    "AI agent review loop",
    "vendor-neutral AI coding",
    "cost arbitrage AI coding",
    "policy-defined agent workflow",
    "TOML agent configuration",
    "parallel AI coding agents",
    "AI agent checkpoint resume",
]


# ── HTTP helpers ────────────────────────────────────────────────────────────────

def http_get(url: str, headers: dict | None = None, timeout: int = 8) -> tuple[int, str]:
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; RalphBot/1.0)"}
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return 0, ""


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return m.group(1).strip() if m else ""


def _extract_meta(html: str, name: str) -> str:
    for p in [
        rf'<meta[^>]+name="{name}"[^>]+content="([^"]+)"',
        rf'<meta[^>]+content="([^"]+)"[^>]+name="{name}"',
    ]:
        m = re.search(p, html, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _extract_description(html: str) -> str:
    """Get meta description and first paragraph as description proxy."""
    desc = _extract_meta(html, "description")
    if desc:
        return desc
    m = re.search(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)
    return m.group(1)[:200].strip() if m else ""


def _has_feature(html: str, feature_keywords: list[str]) -> bool:
    """Check if page mentions any feature keywords."""
    text = html.lower()
    return any(kw.lower() in text for kw in feature_keywords)


# ── Competitor monitoring ──────────────────────────────────────────────────────

def monitor_competitor(slug: str, info: dict) -> dict:
    """Gather intelligence on a single competitor."""
    result = {
        "slug": slug,
        "name": info["name"],
        "url": info["url"],
        "checked_at": datetime.now().isoformat(),
        "site_status": None,
        "title": "",
        "description": "",
        "pricing_visible": info["pricing"],
        "positioning": info["positioning"],
        "key_features_found": [],
        "blog_status": None,
        "github_stars": None,
        "changes": [],
    }

    # Check main site
    status, body = http_get(info["url"])
    result["site_status"] = status
    if status == 200:
        result["title"] = _extract_title(body)
        result["description"] = _extract_description(body)[:300]

        # Check for key features
        found = [f for f in info["key_strengths"] if f.lower() in body.lower()]
        result["key_features_found"] = found

    # Check docs if available
    if info.get("docs_url"):
        doc_status, doc_body = http_get(info["docs_url"])
        if doc_status == 200:
            result["docs_status"] = doc_status
            result["docs_title"] = _extract_title(doc_body)

    # Check GitHub stars if available (fail fast — 5s timeout)
    if info.get("github"):
        stars = get_github_stars(info["github"])
        if stars:
            result["github_stars"] = stars

    return result


def get_github_stars(repo_url: str) -> Optional[int]:
    """Get GitHub star count for a repository. Fails fast — 5s timeout."""
    # Convert URL to API URL
    # e.g. https://github.com/nousresearch/hermes-agent → nousresearch/hermes-agent
    m = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).replace(".git", "")
    api_url = f"https://api.github.com/repos/{owner}/{repo}"

    try:
        req = urllib.request.Request(
            api_url,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "RalphBot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("stargazers_count")
    except Exception:
        return None


def run_competitor_monitoring() -> dict:
    """Monitor all competitors and return a comprehensive report."""
    results = {}
    for slug, info in COMPETITORS.items():
        try:
            results[slug] = monitor_competitor(slug, info)
        except Exception as e:
            results[slug] = {"slug": slug, "error": str(e)}
        # Minimal pacing — just enough to avoid hammering
        time.sleep(0.15)

    # Save snapshot
    today = datetime.now().strftime("%Y-%m-%d")
    snapshot_path = COMPETITOR_REPORTS / f"monitoring_{today}.json"
    snapshot_path.write_text(json.dumps({"timestamp": datetime.now().isoformat(), "competitors": results}, indent=2, default=str))

    return results


# ── Comparison page generator ─────────────────────────────────────────────────

def generate_comparison_page(competitor_slug: str, competitor_info: dict, monitoring_data: dict) -> str:
    """Generate a markdown comparison page for Ralph Workflow vs a competitor."""



    comp_features = competitor_info.get("key_strengths", [])

    # Build markdown
    lines = [
        f"# Ralph Workflow vs {competitor_info['name']}",
        "",
        f"**Last updated:** {datetime.now().strftime('%B %d, %Y')} · "
        f"[Edit this comparison](https://codeberg.org/Ralph-Workflow/Ralph-Workflow) · "
        f"[← Back to all comparisons](/comparisons)",
        "",
        "## At a Glance",
        "",
        f"| | **Ralph Workflow** | **{competitor_info['name']}** |",
        f"|---|---|---|",
        f"| **What it is** | Unattended multi-agent orchestration | {competitor_info['positioning']} |",
        f"| **License** | AGPL (source) / CC0 (outputs) | {competitor_info['pricing']} |",
        f"| **Setup** | TOML config files, no cloud required | Varies |",
        f"| **Vendor lock-in** | None — own your config | {'Yes' if competitor_info['name'] in ['Claude Code', 'GitHub Copilot', 'Cursor'] else 'Varies'} |",
        "",
        "## Key Differences",
        "",
    ]

    # Positioning contrast
    lines.extend([
        f"**Ralph Workflow** is a **{competitor_info['name']}** *alternative* for teams that want:",
        "",
    ])
    lines.append("- Multi-agent orchestration where different phases use different model families")
    lines.append("- Cost control via model routing (cheap models where sufficient)")
    lines.append("- Policy-defined workflows anyone can read and version in git")
    lines.append("- True unattended execution with artifact-based completion criteria")
    lines.append("")
    lines.append(f"**{competitor_info['name']}** is better for:")
    lines.append("")
    for f in comp_features[:4]:
        lines.append(f"- {f}")
    lines.append("")

    # Feature comparison table
    lines.extend([
        "## Feature Comparison",
        "",
        f"| Feature | Ralph Workflow | {competitor_info['name']} |",
        f"|---|---|---|",
        "| Multi-agent orchestration | ✅ | ❌ |" if competitor_info["name"] not in ["Claude Code"] else "| Multi-agent orchestration | ✅ | ⚠️ |",
        "| Claude Code integration | ✅ | ❌ |" if competitor_info["name"] not in ["Claude Code"] else "| Claude Code integration | ✅ | ✅ |",
        "| OpenCode / Codex integration | ✅ | ❌ |" if competitor_info["name"] not in ["Claude Code", "Cursor"] else "| OpenCode / Codex integration | ✅ | ❌ |",
        "| Cost model routing | ✅ | ❌ |",
        "| unattended execution | ✅ | ⚠️ |",
        "| Policy-defined config (TOML) | ✅ | ❌ |",
        "| Checkpoint / resume | ✅ | ⚠️ |",
        "| MCP support | ✅ | ⚠️ |" if competitor_info["name"] not in ["Conductor OSS"] else "| MCP support | ✅ | ✅ |",
        "| Artifact-based completion | ✅ | ❌ |",
        "| Parallel work units | ✅ | ❌ |" if competitor_info["name"] not in ["Conductor OSS", "Continue"] else "| Parallel work units | ✅ | ✅ |",
        "| Open source | ✅ | ✅ |" if competitor_info["pricing"] != "Proprietary" else "| Open source | ✅ | ❌ |",
        "| Self-hosted | ✅ | ⚠️ |",
        "",
    ])

    # Ralph's advantages
    lines.extend([
        "## Why Choose Ralph Workflow Over " + competitor_info["name"],
        "",
    ])
    if competitor_info["name"] in ["Claude Code"]:
        lines.extend([
            f"Claude Code is excellent for interactive, single-agent coding sessions. Ralph Workflow is designed for a",
            "fundamentally different use case: ** unattended multi-phase pipelines** where you define the workflow",
            "once and run it the same way every time.",
            "",
            "Key advantages:",
            "- **Multi-phase routing**: Claude plans, a cheap model writes, Claude reviews, a cheap model fixes",
            "- **Cost arbitrage**: Route work to $0.003/1k tokens models where they're sufficient",
            "- **Policy as code**: Your workflow is a TOML file you diff, version, and audit",
            "- **Unattended by design**: Claude Code is interactive-first; Ralph Workflow is built to walk away from",
            "",
        ])
    elif competitor_info["name"] in ["GitHub Copilot"]:
        lines.extend([
            f"Copilot is deeply integrated into GitHub and IDEs for inline suggestions. Ralph Workflow targets",
            "teams that want to run autonomous agents across entire development phases without human steering.",
            "",
            "Key advantages:",
            "- **Multi-agent > single suggestions**: Copilot suggests; Ralph Workflow completes phases",
            "- **Vendor-neutral**: Not tied to OpenAI or GitHub's model choices",
            "- **Cost routing**: Use cheap models for grunt work, save frontier models for review",
            "- **Unattended execution**: Start a pipeline, come back to reviewed commits",
            "",
        ])
    elif competitor_info["name"] in ["Cursor"]:
        lines.extend([
            f"Cursor is an AI-first IDE. Ralph Workflow is a CLI pipeline that runs headless.",
            "They address different needs: Cursor for interactive editing, Ralph Workflow for automated pipelines.",
            "",
        ])
    elif competitor_info["name"] in ["Aider"]:
        lines.extend([
            f"Aider is a terminal-based pair programming tool. Ralph Workflow is an unattended pipeline runner.",
            "They can actually be used together — Aider as an interactive editor, Ralph Workflow for overnight runs.",
            "",
        ])
    else:
        lines.extend([
            f"{competitor_info['name']} focuses on {competitor_info['positioning']}.",
            f"Ralph Workflow's focus is **multi-agent phase routing with cost arbitrage and policy-defined orchestration**.",
            "",
            "Ralph Workflow's key differentiator is the ability to compose multiple agents (Claude, Codex,",
            "OpenCode) into a single unattended pipeline where each phase uses the most cost-effective model.",
            "",
        ])

    # CTA
    lines.extend([
        "## Try Ralph Workflow",
        "",
        "```bash",
        "pip install ralph-workflow",
        "cd /path/to/your/project",
        "ralph --init",
        "$EDITOR PROMPT.md  # write your task",
        "ralph  # walk away",
        "```",
        "",
        f"[Install guide →](https://ralphworkflow.com/docs) · "
        f"[Quick start →](https://ralphworkflow.com/docs#quick-start) · "
        f"[Primary Codeberg repo →](https://codeberg.org/RalphWorkflow/Ralph-Workflow) · "
        f"[GitHub mirror →](https://github.com/Ralph-Workflow/Ralph-Workflow)",
        "",
    ])

    return "\n".join(lines)


def generate_all_comparison_pages(monitoring_data: dict):
    """Generate comparison pages for all tracked competitors."""
    comparisons_dir = REPORTS_DIR / "comparisons"
    comparisons_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    for slug, info in COMPETITORS.items():
        data = monitoring_data.get(slug, {})
        md = generate_comparison_page(slug, info, data)
        path = comparisons_dir / f"{slug}.md"
        path.write_text(md)
        generated.append((slug, info["name"], path))

    return generated


# ── Competitor discovery ──────────────────────────────────────────────────────

def discover_new_competitors() -> list[dict]:
    """Find new competitors via web search. Falls back to empty list if blocked.
    
    Note: Google search is unreliable for automated scraping. This function
    returns an empty list more often than not. For manual competitor research,
    check the SUMMARY.md in .planning/research/ instead.
    """
    try:
        query = urllib.parse.quote("AI agent workflow orchestration CLI tool 2025")
        url = f"https://www.google.com/search?q={query}&num=10"
        status, body = http_get(url, timeout=5)
        if status != 200 or not body:
            return []

        # Extract competitor domains from search results
        domains = re.findall(r'href="(https?://[^"]+)"', body)
        seen = set()
        competitors = []
        for href in domains:
            domain = urllib.parse.urlparse(href).netloc
            if domain in seen or "ralphworkflow" in domain:
                continue
            if any(kw in domain for kw in ["agent", "ai", "claude", "copilot", "coder", "dev", "tool", "orchestrat"]):
                seen.add(domain)
                competitors.append({"domain": domain, "url": href})

        return competitors[:5]
    except Exception:
        return []


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    script_start = time.time()
    print(f"[competitor_analysis] Starting competitor analysis at {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # 1. Monitor all known competitors
    print("  → Monitoring competitors...")
    t0 = time.time()
    monitoring = run_competitor_monitoring()
    print(f"    done in {time.time()-t0:.1f}s")

    # 2. Generate comparison pages
    print("  → Generating comparison pages...")
    t1 = time.time()
    comparisons = generate_all_comparison_pages(monitoring)
    print(f"    done in {time.time()-t1:.1f}s")

    # 3. Check for new competitors (fast-fail if Google is blocked)
    print("  → Discovering new competitors...")
    t2 = time.time()
    new_competitors = discover_new_competitors()
    print(f"    done in {time.time()-t2:.1f}s (found {len(new_competitors)})")

    # 4. Build summary report
    today = datetime.now().strftime("%Y-%m-%d")
    summary_lines = [
        f"# Competitor Analysis — {today}",
        "",
        "## Monitored Competitors",
        "",
    ]

    for slug, info in COMPETITORS.items():
        data = monitoring.get(slug, {})
        stars = f" ⭐ {data.get('github_stars', '?')}" if data.get('github_stars') else ""
        status = "✅" if data.get('site_status') == 200 else f"❌ ({data.get('site_status')})"
        summary_lines.append(f"- {status} **{info['name']}** ({info['url']}){stars}")

    if new_competitors:
        summary_lines.extend(["", "## Discovered (new)", ""])
        for c in new_competitors:
            summary_lines.append(f"- [{c['domain']}]({c['url']}) — needs evaluation")

    summary_lines.extend(["", "## Comparison Pages", ""])
    for slug, name, path in comparisons:
        summary_lines.append(f"- [{name}](../comparisons/{slug}.md)")

    # Ralph's competitive advantages
    summary_lines.extend([
        "",
        "## Ralph Workflow Key Advantages",
        "",
        "These should be the center of every comparison page and marketing message:",
        "",
    ])
    for adv in RALPH_ADVANTAGE_KEYWORDS[:5]:
        summary_lines.append(f"- **{adv}**")

    summary_path = REPORTS_DIR / f"competitor_analysis_{today}.md"
    summary_path.write_text("\n".join(summary_lines))

    total = time.time() - script_start
    print(f"\nCompetitor analysis complete ({total:.1f}s total):")
    print(f"  - Monitored: {len(monitoring)} competitors")
    print(f"  - Generated: {len(comparisons)} comparison pages")
    print(f"  - Discovered: {len(new_competitors)} new candidates")
    print(f"  - Report: {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
