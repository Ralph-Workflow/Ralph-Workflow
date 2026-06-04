#!/usr/bin/env python3
"""
blind_monitor_replacement.py — DDG health checker + discovery fallback agent.

Purpose:
  web_search (DuckDuckGo) is returning 0 results for all queries since 2026-05-28.
  This agent replaces the passive "suspension" marker with an active health-check
  loop that:
  1. Tests DDG availability with a known-good query every 6 hours
  2. If DDG is down, falls back to Brave Search (HTML scrape, no API key needed)
  3. Surfaces the June 4 escalation state as a human-actionable artifact
  4. Produces a live distribution-feed from whatever sources are available

Integration:
  - Cron: */30 * * * * (every 30 min — lightweight, health-check only)
  - Called by run.py → marketing_loop_runner.py when search_provider_degraded
  - Output feeds into distribution_lane_selector.py as alternative discovery source

Author: marketing self-improvement loop
Created: 2026-06-03T15:10+02:00 (DDG 7-day suspension window — day 6 of 7)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parent.parent.parent  # ~/.openclaw/workspace
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DDG_URL = "https://html.duckduckgo.com/html/"
BRAVE_URL = "https://search.brave.com/search"
KNOWN_GOOD_QUERY = "python programming language"
REDDIT_QUERY = "site:reddit.com AI agent workflow open source 2026"
AGENT_ECOSYSTEM_QUERY = (
    '"ai agent workflow" OR "autonomous coding" composer OR orchestrator open source python'
)

SUSPENSION_MARKER = LOG_DIR / "reddit_monitor_suspension.json"
ESCALATION_DEADLINE = "2026-06-04"  # 7 days from May 28 suspension start

# ── Helpers ──────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]

def _fetch(url: str, timeout: int = 15) -> tuple[int, str]:
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)

def _is_bot_blocked(html: str) -> bool:
    """Detect DDG bot-detection/captcha pages."""
    tl = html.lower()
    markers = [
        "captcha", "verify you are human", "are you a robot",
        "unusual traffic", "automated requests", "challenge",
        "complete the security check", "blocked",
    ]
    return any(m in tl for m in markers)

def _has_results(html: str) -> bool:
    """Check if the DDG/Brave HTML has any result links."""
    return bool(re.search(r'<a[^>]+class="[^"]*result[^"]*"', html, re.IGNORECASE)) or \
           bool(re.search(r'class="result__a"', html)) or \
           bool(re.search(r'class="snippet"', html))

def _extract_urls(html: str, source: str = "ddg") -> list[dict[str, str]]:
    """Extract result URLs + snippets from DDG or Brave HTML."""
    results = []
    if source == "ddg":
        # DDG HTML result blocks
        for match in re.finditer(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.IGNORECASE | re.DOTALL
        ):
            url = match.group(1)
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            # Resolve DDG redirects
            uddg = re.search(r'uddg=([^&]+)', url)
            if uddg:
                url = urllib.parse.unquote(uddg.group(1))
            if url and not url.startswith("http"):
                url = "https:" + url
            if "duckduckgo.com" not in url:
                results.append({"url": url, "title": title})
    elif source == "brave":
        for match in re.finditer(
            r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*snippet[^"]*"[^>]*>',
            html, re.IGNORECASE
        ):
            url = match.group(1)
            if "brave.com" not in url:
                results.append({"url": url, "title": ""})
        # Also try different Brave markup
        for match in re.finditer(
            r'class="result-header"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.IGNORECASE | re.DOTALL
        ):
            url = match.group(1)
            title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            if "brave.com" not in url:
                results.append({"url": url, "title": title})
    return results

# ── Health checks ────────────────────────────────────────────────────────────
def check_ddg() -> dict[str, Any]:
    """Test DDG with a known-good query."""
    result = {
        "provider": "duckduckgo",
        "timestamp": _now_iso(),
        "ok": False,
        "http_status": None,
        "result_count": 0,
        "bot_blocked": False,
        "results": [],
    }

    # Test 1: basic known-good query
    url = f"{DDG_URL}?q={urllib.parse.quote(KNOWN_GOOD_QUERY)}"
    status, html = _fetch(url)
    result["http_status"] = status

    if status == 200:
        result["bot_blocked"] = _is_bot_blocked(html)
        result["results"] = _extract_urls(html, "ddg")
        result["result_count"] = len(result["results"])
        result["ok"] = not result["bot_blocked"] and result["result_count"] > 0
        result["health"] = _classify_ddg_health(result)

    # Test 2: site:reddit.com query (what the monitor needs)
    reddit_url = f"{DDG_URL}?q={urllib.parse.quote(REDDIT_QUERY)}"
    r_status, r_html = _fetch(reddit_url)
    result["reddit_test"] = {
        "ok": r_status == 200 and not _is_bot_blocked(r_html) and _has_results(r_html),
        "http_status": r_status,
    }

    return result


def check_brave() -> dict[str, Any]:
    """Test Brave Search HTML scrape (no API key needed)."""
    result = {
        "provider": "brave",
        "timestamp": _now_iso(),
        "ok": False,
        "http_status": None,
        "result_count": 0,
        "results": [],
    }

    url = f"{BRAVE_URL}?q={urllib.parse.quote(AGENT_ECOSYSTEM_QUERY)}&source=web"
    status, html = _fetch(url)
    result["http_status"] = status

    if status == 200:
        result["results"] = _extract_urls(html, "brave")
        result["result_count"] = len(result["results"])
        result["ok"] = result["result_count"] > 0

    return result


def _classify_ddg_health(check: dict[str, Any]) -> str:
    if check.get("ok"):
        return "healthy"
    if check.get("bot_blocked"):
        return "bot_blocked"
    if check.get("http_status") == 200 and check.get("result_count", 0) == 0:
        return "zero_results"
    if check.get("http_status") == 403:
        return "ip_blocked"
    if check.get("http_status") == 0:
        return "network_error"
    return f"degraded_http_{check.get('http_status')}"


# ── Escalation artifact ──────────────────────────────────────────────────────
def write_escalation_artifact(ddg: dict[str, Any], brave: dict[str, Any]) -> Path:
    """Write the June 4 escalation notification when DDG has been dead 7 days."""
    days_down = (datetime.now(timezone.utc) - datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc)).days
    approaching_deadline = datetime.now(timezone.utc).date() >= datetime(2026, 6, 4).date()

    artifact = LOG_DIR / "ddg_escalation_latest.md"
    lines = [
        "# DDG Search Provider — Escalation Notification",
        f"Generated: {_now_iso()}",
        "",
        "## Status",
        f"- **DDG status**: {_classify_ddg_health(ddg)}",
        f"- **HTTP**: {ddg.get('http_status')}",
        f"- **Results**: {ddg.get('result_count', 0)}",
        f"- **Bot-blocked**: {ddg.get('bot_blocked')}",
        f"- **Reddit query test**: {'PASS' if ddg.get('reddit_test', {}).get('ok') else 'FAIL'}",
        f"- **Brave fallback**: {'working' if brave.get('ok') else 'degraded'} ({brave.get('result_count', 0)} results)",
        f"- **Days since last usable retrieval**: {days_down} (since 2026-05-28)",
        "",
    ]

    if approaching_deadline:
        lines += [
            "## ⚠️ ESCALATION DEADLINE: TODAY (June 4)",
            "",
            "DDG has been completely unresponsive for 7 days. The suspension marker says:",
            '> "If suspension exceeds 7 days (2026-06-04), escalate via user notification and consider provider migration (Brave Search API, SerpAPI, etc.)"',
            "",
            "### Impact",
            "- **Reddit monitor**: dead (no signal since May 28)",
            "- **Publisher discovery**: dead (publisher_discovery_lane.py depends on DDG HTML scrape)",
            "- **SEO indexation diagnostic**: dead (seo_indexation_diagnostic.py depends on DDG)",
            "- **All web_search-driven discovery**: dead",
            "- **Distribution lane selector**: operating blind — cannot find new distribution surfaces",
            "",
            "### What's still working",
            "- Owned conversion surfaces (blog, README, compare page, docs, PyPI, Docker)",
            "- Direct URL access (stackoverflow.com, curated targets in queues)",
            "- Content quality maintenance (conversion_surface_watchdog, social_proof_bootstrap CTA audits)",
            "- Star conversion runner (ralph contribute CLI, runner.py periodic CTA)",
            "- Internal optimization (cross-links, SEO integrity, comparison pages)",
            "",
            "### Recommended actions (human)",
            "1. Configure a Brave Search API key (free tier: 2,000 queries/month) or SerpAPI key",
            "2. Set `BRAVE_API_KEY` or `SERPAPI_KEY` environment variable",
            "3. Or: configure a different search backend in OpenClaw (if supported)",
            "4. Or: run the marketing loop from a non-Hetzner IP (AWS, home connection) to unblock DDG",
            "5. Delete `/agents/marketing/logs/reddit_monitor_suspension.json` to re-enable web_search attempts",
            "",
            "### What happens if unattended",
            "- The system will continue optimizing owned conversion surfaces (blog, README, compare page)",
            "- External distribution will remain limited to: StackOverflow, curated handoff packets, ralph contribute CLI",
            "- No new Reddit/social discovery will surface",
            "- The measurement hold cycle will keep the system from spiraling into fake-progress churn",
            "",
        ]
    else:
        lines += [
            "## Escalation deadline: June 4 (in the future)",
            f"Days remaining: {7 - days_down}",
            "DDG is still down, but the 7-day suspension window has not yet expired.",
            "The system continues with owned-surface optimization and curated-target distribution.",
        ]

    lines += [
        "## Live state",
        "```json",
        json.dumps({
            "ddg": {k: v for k, v in ddg.items() if k != "results"},
            "brave": {k: v for k, v in brave.items() if k != "results"},
        }, indent=2),
        "```",
        "",
        "## Discovered URLs (from working fallback)",
    ]

    urls = brave.get("results", []) if brave.get("ok") else ddg.get("results", [])
    for r in urls[:20]:
        lines.append(f"- [{r.get('title', r.get('url', '?'))}]({r.get('url')})")

    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return artifact


# ── Discovery feed ───────────────────────────────────────────────────────────
def produce_discovery_feed(brave: dict[str, Any]) -> dict[str, Any]:
    """Produce distribution-feed output usable by distribution_lane_selector."""
    feed = {
        "generated_at": _now_iso(),
        "source": "blind_monitor_replacement",
        "ddg_available": False,  # We already know DDG is down
        "brave_available": brave.get("ok", False),
        "channels": [],
    }

    for r in brave.get("results", [])[:20]:
        domain = urllib.parse.urlparse(r.get("url", "")).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        # Classify channels
        channel = "unknown"
        if "reddit.com" in domain:
            channel = "reddit"
        elif any(d in domain for d in ["news.ycombinator.com", "lobste.rs"]):
            channel = "dev_community"
        elif "medium.com" in domain:
            channel = "blog_platform"
        elif "dev.to" in domain:
            channel = "dev_community"
        elif any(d in domain for d in ["stackoverflow.com", "stackexchange.com"]):
            channel = "qa"
        elif ".github.io" in domain or "github.com" in domain:
            channel = "repo"
        elif "pypi.org" in domain:
            channel = "package"
        elif "youtube.com" in domain:
            channel = "video"

        feed["channels"].append({
            "url": r.get("url"),
            "title": r.get("title", ""),
            "domain": domain,
            "channel": channel,
        })

    return feed


# ── Main ─────────────────────────────────────────────────────────────────────
def run(dry_run: bool = False) -> dict[str, Any]:
    """Run the blind monitor health check and produce outputs."""
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()

    # 1. Health checks
    ddg = check_ddg()
    brave = check_brave()

    # 2. Write escalation artifact (always — it's also the status display)
    escalation_path = write_escalation_artifact(ddg, brave)

    # 3. Produce discovery feed if any source is working
    feed = produce_discovery_feed(brave)

    # 4. Write status log
    status = {
        "generated_at": timestamp,
        "agent": "blind_monitor_replacement",
        "dry_run": dry_run,
        "ddg": {
            "ok": ddg["ok"],
            "health": _classify_ddg_health(ddg),
            "result_count": ddg["result_count"],
            "bot_blocked": ddg["bot_blocked"],
            "reddit_ok": ddg.get("reddit_test", {}).get("ok", False),
        },
        "brave": {
            "ok": brave["ok"],
            "result_count": brave["result_count"],
        },
        "escalation_artifact": str(escalation_path),
        "discovery_channels": len(feed.get("channels", [])),
        "recommendation": _recommendation(ddg, brave),
    }

    status_path = LOG_DIR / "blind_monitor_status_latest.json"
    if not dry_run:
        status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    return status


def _recommendation(ddg: dict[str, Any], brave: dict[str, Any]) -> str:
    if ddg.get("ok"):
        return "DDG is responding — Reddit monitor can be re-enabled. Delete the suspension marker."
    if brave.get("ok"):
        return (
            "DDG is dead but Brave HTML scrape yields results. "
            "Reddit_monitor and publisher_discovery should use Brave as fallback source. "
            "Long-term: configure BRAVE_API_KEY for clean JSON results."
        )
    return (
        "Both DDG and Brave are blocked. No search-based discovery is possible from "
        "this environment. All distribution must use curated targets, owned surfaces, "
        "and manual handoff packets. Escalate to human for provider migration."
    )


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    status = run(dry_run=dry_run)
    print(json.dumps(status, indent=2))
