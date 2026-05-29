#!/usr/bin/env python3
"""
Publisher discovery lane — finds fresh articles about AI agent orchestration,
autonomous coding, and unattended coding workflows from sites not yet contacted.

Produces a ranked discovery queue for the next manual-outreach window.
Replaces the directory-submission flood with targeted publisher contact.

Usage: python3 publisher_discovery_lane.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
OUTREACH_LOG = ROOT / "outreach-log.md"
LOG = ROOT / "agents/marketing/logs/publisher_discovery_latest.json"
QUEUE = ROOT / "agents/marketing/logs/publisher_discovery_queue_latest.json"

SEARCH_QUERIES = [
    "AI agent orchestration comparison 2026",
    "autonomous coding workflow open source",
    "unattended coding agent tool comparison",
    "AI coding agent orchestration CLI 2026",
    "multi-agent coding pipeline review",
    "agentic coding framework comparison open source",
]

# Domains already heavily covered in outreach log (to de-duplicate search)
SATURATED_DOMAINS: set[str] = set()


def _load_outreach_log_domains() -> set[str]:
    """Extract domains already contacted from the outreach log."""
    if not OUTREACH_LOG.exists():
        return set()
    text = OUTREACH_LOG.read_text(errors="replace")
    # Find domains in markdown links and bare URLs
    domains: set[str] = set()
    # Email patterns: emailed **SiteName** / contacted **SiteName**
    for m in re.finditer(r'\*\*([A-Za-z][A-Za-z0-9 ._-]+?)\*\*', text):
        name = (m.group(1) or "").strip().lower()
        if name:
            domains.add(name)
    # URL domains
    for m in re.finditer(r'https?://([^/\s\)]+)', text):
        domains.add(m.group(1).lower())
    # Submission names
    for m in re.finditer(r'-\s*\*\*([^*]+?)\*\*\s*—\s*directory submission|publisher outreach|contact form', text):
        name = m.group(1)
        if name:
            domains.add(name.strip().lower())
    return domains


def _extract_ddg_url(raw_href: str) -> tuple[str, str]:
    """Extract the real target URL and domain from a DDG redirect link.
    Returns (real_url, domain).
    """
    # DDG redirect format: //duckduckgo.com/l/?uddg=<encoded_url>&...
    ddg_match = re.search(r'uddg=([^&]+)', raw_href)
    if ddg_match:
        try:
            real_url = urllib.parse.unquote(ddg_match.group(1))
        except Exception:
            real_url = raw_href
    else:
        real_url = raw_href
    # Extract domain
    domain_match = re.match(r'https?://([^/]+)', real_url)
    domain = domain_match.group(1) if domain_match else real_url
    return real_url, domain


def _search_web(query: str) -> list[dict]:
    """Minimal web search for articles. Returns list of {title, url, source}."""
    try:
        encoded = urllib.parse.quote(query)
        req = urllib.request.Request(
            f"https://html.duckduckgo.com/html/?q={encoded}",
            headers={"User-Agent": "RalphWorkflow/1.0 (marketing-research)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode(errors="replace")
    except Exception:
        return []

    results: list[dict] = []
    for m in re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL,
    ):
        raw_href = m.group(1)
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        # Skip empty titles
        if not title:
            continue
        real_url, domain = _extract_ddg_url(raw_href)
        # Skip if URL is still a search engine redirect without real content
        if "duckduckgo.com" in domain or not domain or "//" in domain:
            continue
        results.append({"title": title, "url": real_url, "source": domain})
    return results


def discover(saturated: set[str] | None = None) -> list[dict]:
    """Run discovery across all search queries."""
    if saturated is None:
        saturated = _load_outreach_log_domains()

    all_results: list[dict] = []
    seen_urls: set[str] = set()

    for query in SEARCH_QUERIES:
        results = _search_web(query)
        for r in results:
            if r["url"] in seen_urls:
                continue
            seen_urls.add(r["url"])
            # Skip saturated domains
            skip = False
            for sat in saturated:
                if sat.lower() in r["source"].lower() or sat.lower() in r["title"].lower():
                    skip = True
                    break
            if not skip:
                all_results.append({**r, "query": query})

    return all_results


def rank(discovered: list[dict]) -> list[dict]:
    """Rank by fresh-domain priority and title relevance."""
    scored = []
    for item in discovered:
        score = 0
        title_lower = item["title"].lower()
        if any(kw in title_lower for kw in ["comparison", "vs", "versus", "alternative"]):
            score += 3
        if any(kw in title_lower for kw in ["orchestration", "orchestrator", "pipeline"]):
            score += 2
        if any(kw in title_lower for kw in ["review", "guide", "tutorial", "walkthrough"]):
            score += 2
        if any(kw in title_lower for kw in ["open source", "oss", "free"]):
            score += 1
        if any(kw in title_lower for kw in ["agent", "coding", "workflow", "unattended"]):
            score += 1
        scored.append({**item, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def main() -> int:
    saturated = _load_outreach_log_domains()
    discovered = discover(saturated)
    ranked = rank(discovered)

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "saturated_domains_count": len(saturated),
        "discovered_count": len(discovered),
        "ranked": ranked[:10],  # top 10
        "next_action": (
            "Review the ranked list for publisher outreach candidates."
            if ranked else "No fresh publishers found. Try broadening search queries."
        ),
    }

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "w") as f:
        json.dump(result, f, indent=2, default=str)
    with open(QUEUE, "w") as f:
        json.dump(ranked[:15], f, indent=2, default=str)

    print(json.dumps({
        "status": "ok" if ranked else "empty",
        "discovered": len(discovered),
        "ranked_top": len(ranked[:10]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
