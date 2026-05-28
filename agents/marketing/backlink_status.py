#!/usr/bin/env python3
"""Autonomous backlink indexing tracker for RalphWorkflow.

Tracks whether submitted directory listings (AIToolsIndex, ToolShelf, SaaSHub,
ToolWise, MadeWithStack, DevTool Center) have been indexed by search engines.

Run: python3 backlink_status.py
Output: agents/marketing/logs/backlink_status_latest.json
"""
from datetime import UTC, datetime
from html import unescape
import json
import re
import subprocess
import sys
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

PRODUCT_MARKERS = (
    "ralph workflow",
    "ralph-workflow",
)

NEGATIVE_LISTING_MARKERS = (
    "tool not found",
    "page not found",
    "404",
    "not found",
)

TRANSIENT_LISTING_MARKERS = (
    "loading...",
    "loading…",
)

SCRIPT_NAME = Path(__file__).name
ROOT = Path("/home/mistlight/.openclaw/workspace")
OUT_FILE = ROOT / "agents/marketing/logs/backlink_status_latest.json"
LOG_FILE = ROOT / "agents/marketing/logs/backlink_indexing_log.jsonl"

# The submissions we've made and their known listing/check URLs.
# These should reflect current live runtime truth rather than stale historical guesses.
SUBMISSIONS = {
    "AIToolsIndex": {
        "submit_url": "https://aitoolsindex.org/submit",
        "listing_url": "https://aitoolsindex.org/tools/ralph-workflow",
        "known_check_urls": [
            "https://aitoolsindex.org/ai-tools/ralph-workflow",
            "https://aitoolsindex.org/tools/ralph-workflow",
        ],
        "status_note": "Submission was confirmed via the public .org API on 2026-05-20; treat as submitted/in-review unless a live listing appears.",
    },
    "ToolShelf": {
        "submit_url": "https://toolshelf.io/submit",
        "listing_url": "https://toolshelf.io/tool/ralph-workflow",
        "known_check_urls": [
            "https://toolshelf.io/tools/ralph-workflow",
        ],
    },
    "SaaSHub": {
        "submit_url": "https://saashub.com/add_url",
        "listing_url": "https://saashub.com/ralph-workflow",
        "known_check_urls": [
            "https://saashub.com/ralph-workflow",
        ],
        "secondary_check_urls": [
            "https://www.saashub.com/ralph-workflow-alternatives",
            "https://www.saashub.com/best-workflow-automation-software",
        ],
    },
    "ToolWise": {
        "submit_url": "https://toolwise.ai/submit-tool",
        "listing_url": "https://toolwise.ai/tools/ralph-workflow",
        "known_check_urls": [
            "https://toolwise.ai/tools/ralph-workflow",
        ],
        "status_note": "Existing ToolWise listing already live and pointing to the primary Codeberg repo.",
    },
    "MadeWithStack": {
        "submit_url": "https://www.madewithstack.com/submit",
        "listing_url": "https://www.madewithstack.com/project/ralph-workflow-1",
        "known_check_urls": [
            "https://www.madewithstack.com/project/ralph-workflow-1",
        ],
        "status_note": "Submission returned slug ralph-workflow-1 with UNDER_EDITORIAL_REVIEW on 2026-05-23; treat as submitted/in-review until the public listing resolves.",
    },
    "DevToolCenter": {
        "submit_url": "https://www.devtool.center/submit",
        "listing_url": "https://www.devtool.center/tools/ralph-workflow",
        "known_check_urls": [
            "https://www.devtool.center/tools/ralph-workflow",
        ],
        "status_note": "Submission returned 201 pending on 2026-05-23 via the public devshelf-backend.onrender.com API; treat as submitted/in-review until the public listing resolves.",
    },
    "AIToolboard": {
        "submit_url": "https://aitoolboard.com/submit",
        "listing_url": "https://aitoolboard.com/tools/ralph-workflow",
        "known_check_urls": [
            "https://aitoolboard.com/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-23 via API; slug=ralph-workflow; pending editorial review.",
    },
    "IndieStack": {
        "submit_url": "https://indiestack.ai/submit",
        "listing_url": "https://indiestack.ai/tools/ralph-workflow",
        "known_check_urls": [
            "https://indiestack.ai/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-23; confirmation page confirmed; pending editorial review.",
    },
    "ListYourTool": {
        "submit_url": "https://www.listyourtool.com/submit-tool",
        "listing_url": "https://www.listyourtool.com/tools/ralph-workflow",
        "known_check_urls": [
            "https://www.listyourtool.com/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-23 via API; tool_id=e27adedd-3faa-432b-ab35-000a8bf66121; pending review.",
    },
    "NavAI": {
        "submit_url": "https://nav-ai.net/submit",
        "listing_url": "https://nav-ai.net/tools/ralph-workflow",
        "known_check_urls": [
            "https://nav-ai.net/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-23 via Supabase REST API; HTTP 201; pending review.",
    },
    "OpenAgents": {
        "submit_url": "https://www.openagents.pro/submit",
        "listing_url": "https://www.openagents.pro/tools/ralph-workflow",
        "known_check_urls": [
            "https://www.openagents.pro/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-23 via Formspree; HTTP 200 ok=true; pending review.",
    },
    "ToolScout": {
        "submit_url": "https://toolscout.ai/submit",
        "listing_url": "https://toolscout.ai/tools/ralph-workflow",
        "known_check_urls": [
            "https://toolscout.ai/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-23 via Bubble form; success screen rendered; pending review.",
    },
    "VBWebTools": {
        "submit_url": "https://www.vbwebtools.com/submit-tool/",
        "listing_url": "https://www.vbwebtools.com/tools/ralph-workflow",
        "known_check_urls": [
            "https://www.vbwebtools.com/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-23 via WordPress AJAX endpoint; HTTP 200; pending 2-3 day review.",
    },
    "TheToolify": {
        "submit_url": "https://submit.thetoolify.dev/",
        "listing_url": "https://submit.thetoolify.dev/tools/ralph-workflow",
        "known_check_urls": [
            "https://submit.thetoolify.dev/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-23 via headless browser; HTTP 200 success; pending review.",
    },
    "Claudetory": {
        "submit_url": "https://claudetory.com/submit",
        "listing_url": "https://claudetory.com/tools/ralph-workflow",
        "known_check_urls": [
            "https://claudetory.com/tools/ralph-workflow",
        ],
        "status_note": "Submitted 2026-05-24 via the public /api/submit-resource endpoint; treat as pending review until the public tool page resolves.",
    },
    "ClaudeCodeAlternatives": {
        "submit_url": "https://claude-code-alternatives.com/tool/create/",
        "listing_url": "https://claude-code-alternatives.com/cli-agents/ralph-workflow/",
        "known_check_urls": [
            "https://claude-code-alternatives.com/cli-agents/ralph-workflow/",
            "https://claude-code-alternatives.com/ai-ides/ralph-workflow/",
            "https://claude-code-alternatives.com/cli-agents/",
            "https://claude-code-alternatives.com/ai-ides/",
        ],
        "status_note": "Submitted 2026-05-24; initial POST returned HTTP 302 and an immediate second attempt said the Codeberg URL was already taken, so treat as recorded/pending until a public listing resolves.",
    },
    "ClaudeStack": {
        "submit_url": "https://www.claudestack.dev/submit",
        "listing_url": "https://claudestack.dev/entries/ralph-workflow",
        "known_check_urls": [
            "https://claudestack.dev/entries/ralph-workflow",
            "https://claudestack.dev/category/workflows",
        ],
        "status_note": "Submitted 2026-05-24 via the public /api/submit endpoint; track the workflows category and the likely slug until the public entry resolves.",
    },
    "AiAgentsDirectory": {
        "submit_url": "https://aiagents.directory/submit/",
        "listing_url": "https://aiagents.directory/ralph-workflow/",
        "known_check_urls": [
            "https://aiagents.directory/ralph-workflow/",
            "https://aiagents.directory/categories/open-source/",
            "https://aiagents.directory/categories/developer-tools/",
        ],
        "status_note": "Submitted 2026-05-24 via the public Django form on /submit/ with Codeberg as the primary URL; treat as pending review until a public listing resolves.",
    },
}

# Phrases that should appear if the backlink is indexed
CHECK_URLS = [
    "https://codeberg.org/RalphWorkflow/Ralph-Workflow",
    "https://github.com/Ralph-Workflow/Ralph-Workflow",
    "ralphworkflow.com",
]

PRIMARY_REPO_URL = CHECK_URLS[0]
MIRROR_REPO_URL = CHECK_URLS[1]
SITE_URL = CHECK_URLS[2]

SEARCH_QUERIES = [
    "site:codeberg.org RalphWorkflow",
    "site:github.com RalphWorkflow Ralph Workflow",
    "ralphworkflow.com",
    "ralph workflow aitoolsindex",
    "ralph workflow toolshelf",
    "ralph workflow saashub",
    "ralph workflow toolwise",
    "ralph workflow indie stack",
    "ralph workflow listyourtool",
    "ralph workflow nav-ai",
    "ralph workflow openagents",
    "ralph workflow toolscout",
    "ralph workflow vbwebtools",
    "ralph workflow the toolify",
    "ralph workflow claudetory",
    "ralph workflow claude code alternatives",
    "ralph workflow claudestack",
    "ralph workflow aiagents.directory",
]


def _visible_text(body: str) -> str:
    without_scripts = re.sub(r"<script\b[^>]*>.*?</script>", " ", body, flags=re.I | re.S)
    without_styles = re.sub(r"<style\b[^>]*>.*?</style>", " ", without_scripts, flags=re.I | re.S)
    without_tags = re.sub(r"<[^>]+>", " ", without_styles)
    text = unescape(without_tags)
    return re.sub(r"\s+", " ", text).strip()


def _body_quality_flags(body: str) -> dict:
    visible = _visible_text(body).lower()
    has_product_marker = any(marker in visible for marker in PRODUCT_MARKERS)
    negative_markers = [marker for marker in NEGATIVE_LISTING_MARKERS if marker in visible]
    transient_markers = [marker for marker in TRANSIENT_LISTING_MARKERS if marker in visible]
    return {
        "has_product_marker": has_product_marker,
        "negative_markers": negative_markers,
        "transient_markers": transient_markers,
    }


def _extract_links(body: str) -> list[str]:
    links = re.findall(r'href=["\']([^"\']+)["\']', body, flags=re.I)
    deduped: list[str] = []
    seen: set[str] = set()
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        deduped.append(link)
    return deduped


def _listing_targets(links: list[str]) -> dict:
    lowered = [link.lower() for link in links]
    has_codeberg = any("codeberg.org/ralphworkflow/ralph-workflow" in link for link in lowered)
    has_github = any("github.com/ralph-workflow/ralph-workflow" in link for link in lowered)
    has_site = any("ralphworkflow.com" in link for link in lowered)

    preferred_repo_target = "unknown"
    if has_codeberg and not has_github:
        preferred_repo_target = "codeberg_primary"
    elif has_github and not has_codeberg:
        preferred_repo_target = "github_only"
    elif has_codeberg and has_github:
        preferred_repo_target = "both"

    return {
        "has_codeberg_repo_link": has_codeberg,
        "has_github_repo_link": has_github,
        "has_site_link": has_site,
        "preferred_repo_target": preferred_repo_target,
    }


def check_url_status(url: str) -> dict:
    """Check HTTP status of a URL and basic listing truthfulness."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            quality = _body_quality_flags(body)
            targets = _listing_targets(_extract_links(body))
            return {
                "url": url,
                "status": resp.status,
                "ok": True,
                "has_product_marker": quality["has_product_marker"],
                "negative_markers": quality["negative_markers"],
                "transient_markers": quality["transient_markers"],
                **targets,
            }
    except urllib.error.HTTPError as e:
        return {"url": url, "status": e.code, "ok": False, "error": str(e)}
    except Exception as e:
        return {"url": url, "status": None, "ok": False, "error": str(e)}


def check_google_index(query: str) -> dict:
    """Check if a query returns RalphWorkflow results via web search."""
    try:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded}&hl=en"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; backlink-tracker/1.0)",
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            found = []
            for check_url in CHECK_URLS:
                if check_url in body:
                    found.append(check_url)
            return {
                "query": query,
                "indexed": len(found) > 0,
                "found_urls": found,
                "status": resp.status,
            }
    except Exception as e:
        return {"query": query, "indexed": None, "error": str(e)}


def _google_rate_limited(result: dict) -> bool:
    error = str(result.get("error", "")).lower()
    return "429" in error or "too many requests" in error


def skipped_google_index(query: str, reason: str) -> dict:
    return {
        "query": query,
        "indexed": None,
        "skipped": True,
        "error": reason,
    }


def check_listing_status(name: str, info: dict) -> dict:
    """Check if a directory listing page is live."""
    results = []
    for url in info.get("known_check_urls", []):
        result = check_url_status(url)
        results.append(result)
    secondary_results = []
    for url in info.get("secondary_check_urls", []):
        result = check_url_status(url)
        secondary_results.append(result)
    any_live = any(
        r.get("ok")
        and r.get("status") == 200
        and r.get("has_product_marker")
        and not r.get("negative_markers")
        and not (r.get("transient_markers") and not r.get("has_product_marker"))
        for r in results
    )
    live_secondary_results = [
        r
        for r in secondary_results
        if r.get("ok")
        and r.get("status") == 200
        and r.get("has_product_marker")
        and not r.get("negative_markers")
        and not (r.get("transient_markers") and not r.get("has_product_marker"))
    ]
    live_results = [
        r
        for r in results
        if r.get("ok")
        and r.get("status") == 200
        and r.get("has_product_marker")
        and not r.get("negative_markers")
        and not (r.get("transient_markers") and not r.get("has_product_marker"))
    ]
    aggregated_targets = _listing_targets(
        [
            url
            for result in live_results
            for url, present in (
                (PRIMARY_REPO_URL, result.get("has_codeberg_repo_link")),
                (MIRROR_REPO_URL, result.get("has_github_repo_link")),
                (SITE_URL, result.get("has_site_link")),
            )
            if present
        ]
    )
    payload = {
        "directory": name,
        "listing_url": info["listing_url"],
        "submit_url": info["submit_url"],
        "check_results": results,
        "secondary_check_results": secondary_results,
        "listing_live": any_live,
        "secondary_live_surfaces": len(live_secondary_results),
        **aggregated_targets,
    }
    if live_secondary_results:
        payload["secondary_surface_targets"] = [
            {
                "url": result["url"],
                "has_codeberg_repo_link": result.get("has_codeberg_repo_link", False),
                "has_github_repo_link": result.get("has_github_repo_link", False),
                "has_site_link": result.get("has_site_link", False),
                "preferred_repo_target": result.get("preferred_repo_target", "unknown"),
            }
            for result in live_secondary_results
        ]
    if info.get("status_note"):
        payload["status_note"] = info["status_note"]
    return payload


def main():
    now = datetime.now(UTC).isoformat()
    print(f"[{now}] Backlink status check starting...")

    results = {
        "generated_at": now,
        "source": "backlink_status.py",
        "directories": {},
        "google_index": {},
        "summary": {
            "directories_with_live_listings": 0,
            "live_listings_pointing_to_codeberg": 0,
            "live_listings_pointing_to_github_only": 0,
            "live_listings_with_unknown_repo_target": 0,
            "secondary_live_surfaces": 0,
            "secondary_surfaces_pointing_to_codeberg": 0,
            "secondary_surfaces_pointing_to_github_only": 0,
            "secondary_surfaces_with_unknown_repo_target": 0,
            "queries_indexed": 0,
            "queries_unavailable": 0,
            "queries_skipped_after_rate_limit": 0,
            "google_rate_limit_encountered": False,
            "total_queries": len(SEARCH_QUERIES),
            "check_urls": CHECK_URLS,
        },
    }

    # Check each directory listing
    for name, info in SUBMISSIONS.items():
        dir_result = check_listing_status(name, info)
        results["directories"][name] = dir_result
        if dir_result["listing_live"]:
            results["summary"]["directories_with_live_listings"] += 1
            target = dir_result.get("preferred_repo_target")
            if target in {"codeberg_primary", "both"}:
                results["summary"]["live_listings_pointing_to_codeberg"] += 1
            elif target == "github_only":
                results["summary"]["live_listings_pointing_to_github_only"] += 1
            else:
                results["summary"]["live_listings_with_unknown_repo_target"] += 1
        for surface in dir_result.get("secondary_surface_targets", []):
            results["summary"]["secondary_live_surfaces"] += 1
            target = surface.get("preferred_repo_target")
            if target in {"codeberg_primary", "both"}:
                results["summary"]["secondary_surfaces_pointing_to_codeberg"] += 1
            elif target == "github_only":
                results["summary"]["secondary_surfaces_pointing_to_github_only"] += 1
            else:
                results["summary"]["secondary_surfaces_with_unknown_repo_target"] += 1
        print(f"  {name}: listing_live={dir_result['listing_live']}")

    # Check Google index for our URLs
    indexed_queries = 0
    unavailable_queries = 0
    skipped_queries = 0
    google_rate_limited = False
    for query in SEARCH_QUERIES:
        if google_rate_limited:
            idx_result = skipped_google_index(
                query,
                "Skipped after earlier Google 429 to avoid hammering the rate-limited endpoint.",
            )
            skipped_queries += 1
            unavailable_queries += 1
        else:
            idx_result = check_google_index(query)
            indexed = idx_result.get("indexed")
            if indexed is True:
                indexed_queries += 1
            elif indexed is None:
                unavailable_queries += 1
            if _google_rate_limited(idx_result):
                google_rate_limited = True
        results["google_index"][query] = idx_result
        print(f"  google: {query[:50]}... indexed={idx_result.get('indexed')}")

    results["summary"]["queries_indexed"] = indexed_queries
    results["summary"]["queries_unavailable"] = unavailable_queries
    results["summary"]["queries_skipped_after_rate_limit"] = skipped_queries
    results["summary"]["google_rate_limit_encountered"] = google_rate_limited

    # Append to log
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps({"ts": now, "summary": results["summary"]}) + "\n")

    # Write latest
    with open(OUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(
        f"Done. {results['summary']['directories_with_live_listings']} live listings, "
        f"{results['summary']['secondary_live_surfaces']} live secondary surfaces, "
        f"{indexed_queries}/{len(SEARCH_QUERIES)} queries indexed, "
        f"{unavailable_queries} unavailable."
    )
    print(f"Output: {OUT_FILE}")
    return results


# ── Self-improvement ──────────────────────────────────────────────────────────

SELF_IMPROVEMENT_MANDATE = {
    "owner": "marketing_system",
    "goal": "improve real marketing outcomes, not just technical health",
    "default_decision_rule": "it is up to the system to decide and proceed",
    "required_when_outcomes_flat": [
        "create_new_agents",
        "repair_existing_agents",
        "rewrite_prompts",
        "change_cron_jobs",
        "patch_marketing_scripts",
        "add_or_tighten_tests",
        "retire_stale_paths",
        "generate_new_distribution_assets",
    ],
    "technical_repairs_alone_are_insufficient": True,
}

# Note: backlink_status.py is a reporter/checker, not a direct loop driver.
# Self-improvement for reporters means: if outcomes are flat, the reporter's
# findings should inform what the system changes (e.g., expand submission targets).

def flat_outcome_count(script_name: str, max_runs: int = 3) -> int:
    log_path = Path('/home/mistlight/.openclaw/workspace/outreach-log.md')
    if not log_path.exists():
        return 0
    text = log_path.read_text()
    import re
    entries = re.findall(rf'###\s+.*?{re.escape(script_name)}.*?(?=\n###|\Z)', text, re.DOTALL)
    flat_count = sum(1 for e in entries if 'no measurable outcome' in e.lower() or 'flat' in e.lower())
    return min(flat_count, max_runs)


def should_self_improve() -> bool:
    """Return True if this loop has had flat outcomes for 3+ consecutive runs."""
    return flat_outcome_count(SCRIPT_NAME.replace('.py','')) >= 3


if __name__ == "__main__":
    main()
