#!/usr/bin/env python3
"""Daily metrics collector and weekly evaluator for RalphWorkflow marketing.

Self-improving SEO loop:
- Runs seo_daily.py for fresh SEO intelligence every day
- Runs competitor_analysis.py on Mondays
- Loads prior SEO reports to detect trends (rank changes, score changes)
- Makes weekly decisions based on what improved / what degraded
- Feeds insights back into content strategy via STRATEGY.md
- Content generation is guided by actual SEO data, not guesswork
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing")
LOG_DIR = AGENTS_DIR / "logs"
STRATEGY_FILE = AGENTS_DIR / "STRATEGY.md"
POSTED_FILE = LOG_DIR / "posted_urls.json"
SEO_REPORTS_DIR = Path("/home/mistlight/.openclaw/workspace/seo-reports")
ADOPTION_FILE = LOG_DIR / "adoption_metrics_latest.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BLOCKED_CHANNELS = {
    "dev.to": "Needs API key or OAuth",
    "twitter": "Login/session blocked",
    "reddit": "Posting blocked until the intended main Reddit account is verified live",
    "hackernews": "Needs account",
    "lobsters": "Needs invite/account",
    "producthunt": "Protected flow / manual launch required",
}

RALPH_ADVANTAGES = [
    "Multi-agent phase routing (planning → development → review → fix)",
    "Cost arbitrage: Claude + Codex + OpenCode in the same pipeline",
    "Policy-defined orchestration via TOML configuration",
    "True unattended execution with artifact-based completion",
    "Vendor-neutral: own your config, not the tool",
]


# ── Run seo_daily.py ──────────────────────────────────────────────────────────

def run_seo_daily() -> dict:
    """Run seo_daily.py and parse its JSON summary output."""
    result = subprocess.run(
        [sys.executable, str(AGENTS_DIR / "seo_daily.py")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        text = result.stdout.strip()
        if text.startswith("["):
            text = text[text.index("{"):]
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": f"Failed to parse seo_daily JSON: {exc}", "stdout": result.stdout[-500:] if result.stdout else "", "stderr": result.stderr[-500:] if result.stderr else ""}


def run_competitor_analysis() -> dict:
    """Run competitor_analysis.py and parse its output."""
    result = subprocess.run(
        [sys.executable, str(AGENTS_DIR / "competitor_analysis.py")],
        capture_output=True,
        text=True,
        timeout=180,
    )
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = SEO_REPORTS_DIR / f"competitor_analysis_{today}.md"
    summary = {
        "ran_ok": result.returncode == 0,
        "report_path": str(report_path) if report_path.exists() else None,
        "stdout": result.stdout[:300] if result.stdout else "",
    }

    # Parse monitoring snapshot
    snapshot_path = SEO_REPORTS_DIR / "competitors" / f"monitoring_{today}.json"
    if snapshot_path.exists():
        try:
            data = json.loads(snapshot_path.read_text())
            competitors = data.get("competitors", {})
            summary["competitor_count"] = len(competitors)
            summary["competitors"] = {
                slug: {
                    "status": c.get("site_status"),
                    "stars": c.get("github_stars"),
                    "features_found": c.get("key_features_found", [])[:3],
                }
                for slug, c in competitors.items()
                if not c.get("error")
            }
        except Exception:
            pass

    # Parse comparison page count
    comparisons_dir = SEO_REPORTS_DIR / "comparisons"
    if comparisons_dir.exists():
        pages = list(comparisons_dir.glob("*.md"))
        summary["comparison_pages"] = len(pages)
        summary["comparison_pages_list"] = [p.stem for p in pages]

    return summary


# ── Load SEO trend data ───────────────────────────────────────────────────────

def load_seo_trends(days: int = 14) -> dict:
    """Load recent SEO logs to compute week-over-week trends."""
    trends = []
    cutoff = datetime.now() - timedelta(days=days)
    for log_file in sorted(LOG_DIR.glob("seo_*.json")):
        try:
            dt = datetime.fromisoformat(log_file.stem.replace("seo_", ""))
            if dt >= cutoff:
                data = json.loads(log_file.read_text(encoding="utf-8"))
                trends.append(data)
        except Exception:
            continue
    return trends


def compute_trends(current: dict, history: list[dict]) -> dict:
    """Compute week-over-week deltas for key SEO metrics."""
    if not history:
        return {"note": "Not enough history to compute trends yet."}

    prev_ranks = []
    prev_bl = []
    for d in history:
        rank_count = sum(1 for v in d.get("ranks", {}).values() if isinstance(v, dict) and v.get("position"))
        prev_ranks.append(rank_count)
        bl_val = d.get("backlinks", {})
        if isinstance(bl_val, dict):
            prev_bl.append(bl_val.get("count_approx", 0))
        else:
            prev_bl.append(0)

    current_ranks = sum(1 for v in current.get("ranks", {}).values() if isinstance(v, dict) and v.get("position"))
    current_bl = current.get("backlinks", {}).get("count_approx", 0) if isinstance(current.get("backlinks"), dict) else current.get("backlinks_approx", 0)
    current_dr = current.get("domain_rating")

    rank_delta = current_ranks - (sum(prev_ranks) / len(prev_ranks) if prev_ranks else current_ranks)
    bl_delta = current_bl - (sum(prev_bl) / len(prev_bl) if prev_bl else current_bl)

    return {
        "rank_delta": round(rank_delta, 1),
        "backlinks_delta": round(bl_delta, 1),
        "domain_rating": current_dr,
        "prev_avg_ranks": round(sum(prev_ranks) / len(prev_ranks), 1) if prev_ranks else None,
        "prev_avg_bl": round(sum(prev_bl) / len(prev_bl), 1) if prev_bl else None,
    }


# ── HTTP helpers ─────────────────────────────────────────────────────────────

# ── Adoption (repo traffic) helpers ───────────────────────────────────────

def load_adoption_data() -> dict | None:
    """Load the latest adoption_metrics_latest.json if available."""
    if not ADOPTION_FILE.exists():
        return None
    try:
        return json.loads(ADOPTION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def adoption_is_flat(adoption: dict | None) -> bool:
    """Return True if the primary repo (Codeberg) shows no delta in the recent window."""
    if not adoption:
        return False
    eval_data = adoption.get("evaluation", {})
    failing_signals = eval_data.get("failing_signals", [])
    return "primary_repo_flat" in failing_signals


def adoption_flat_reason(adoption: dict | None) -> str:
    if not adoption:
        return "no adoption data available"
    recent = adoption.get("recent_window", {})
    cb = recent.get("Codeberg", {})
    window_samples = cb.get("samples", 0)
    return (
        f"Codeberg repo adoption flat across {window_samples} samples "
        f"(stars {cb.get('stars_delta_window', 0):+d}, "
        f"watchers {cb.get('watchers_delta_window', 0):+d}, "
        f"forks {cb.get('forks_delta_window', 0):+d})"
    )


def http_status(url: str) -> dict:
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return {"ok": True, "status": resp.status, "method": method}
        except Exception as exc:
            last_error = str(exc)
    return {"ok": False, "error": last_error}


def fetch_writeas_views(url: str) -> int:
    page_url = url.replace(".md", "")
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r"(\d+)\s+views?", html)
        return int(match.group(1)) if match else 0
    except Exception:
        return 0


# ── Post performance helpers ─────────────────────────────────────────────────

def load_posted_records() -> list[dict]:
    if not POSTED_FILE.exists():
        return []
    try:
        payload = json.loads(POSTED_FILE.read_text(encoding="utf-8"))
        return payload.get("posts", [])
    except json.JSONDecodeError:
        return []


def parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None


def recent_successful_posts(posts: Iterable[dict], now: datetime, days: int = 30) -> list[dict]:
    cutoff = now - timedelta(days=days)
    selected = []
    for post in posts:
        if not post.get("ok"):
            continue
        dt = parse_iso_date(post.get("timestamp") or post.get("date"))
        if dt is None:
            dt = now
        if dt >= cutoff:
            selected.append(dict(post))
    return selected


def enrich_posts_with_views(posts: list[dict]) -> list[dict]:
    enriched = []
    for post in posts:
        item = dict(post)
        item["views"] = fetch_writeas_views(post.get("url", "")) if post.get("url") else 0
        enriched.append(item)
    return enriched


def summarize_content_performance(posts: list[dict]) -> dict[str, dict]:
    buckets: dict[str, dict] = defaultdict(lambda: {"posts": 0, "views": 0, "avg_views": 0.0})
    for post in posts:
        key = post.get("content_type") or "unknown"
        buckets[key]["posts"] += 1
        buckets[key]["views"] += int(post.get("views", 0))
    for key, data in buckets.items():
        if data["posts"]:
            data["avg_views"] = round(data["views"] / data["posts"], 2)
    return dict(sorted(buckets.items()))


# ── Strategy updates ─────────────────────────────────────────────────────────

def load_strategy_content() -> str:
    if not STRATEGY_FILE.exists():
        return ""
    return STRATEGY_FILE.read_text(encoding="utf-8")


def build_weekly_decisions(
    content_summary: dict[str, dict],
    seo_trends: dict,
    seo_current: dict,
    seo_actions: list[str],
    competitor_data: dict | None = None,
    adoption_data: dict | None = None,
) -> list[dict]:
    """Build weekly decisions informed by actual SEO data + content performance + competitor intelligence + repo adoption."""
    decisions: list[dict] = []

    # Repo adoption gate — Codeberg flat means current tactics are failing
    if adoption_is_flat(adoption_data):
        reason = adoption_flat_reason(adoption_data)
        decisions.append({
            "priority": "high",
            "action": "MARK AS FAILING: Current content/distribution tactics are not driving repo adoption.",
            "reason": reason,
            "repair": "Replace current content format/distribution approach. Stop write.as-only publishing. Try: (a) direct repo README/CONTRIBUTING improvement, (b) SEO landing pages targeting repo-specific terms, (c) cross-post strategy with explicit Codeberg CTAs.",
            "is_failing_signal": True,
        })

    # SEO health gate
    onpage_score_raw = seo_current.get("onpage_score", "?/100")
    if isinstance(onpage_score_raw, str) and "/" in onpage_score_raw:
        try:
            score = int(onpage_score_raw.split("/")[0])
            if score < 75:
                decisions.append({
                    "priority": "high",
                    "action": "Fix on-page SEO issues before investing in new content.",
                    "reason": f"SEO score is {score}/100 — technical foundation needs attention.",
                })
        except ValueError:
            pass

    # Content performance
    ranked = sorted(content_summary.items(), key=lambda item: item[1].get("avg_views", 0), reverse=True)
    if ranked:
        best_type, best_stats = ranked[0]
        decisions.append({
            "priority": "medium",
            "action": f"Keep publishing {best_type} content.",
            "reason": f"Best avg views: {best_stats.get('avg_views', 0):.1f} — lean into what's working.",
        })
        if len(ranked) > 1:
            worst_type, worst_stats = ranked[-1]
            if best_stats.get("avg_views", 0) >= max(1.5 * worst_stats.get("avg_views", 0), 1):
                decisions.append({
                    "priority": "medium",
                    "action": f"Shift one future slot away from {worst_type} toward {best_type}.",
                    "reason": f"{best_type} outperforms {worst_type} on avg views.",
                })

    # SEO trend-based decisions
    rank_delta = seo_trends.get("rank_delta", 0)
    if rank_delta > 0:
        decisions.append({
            "priority": "medium",
            "action": "Rankings improving — maintain content cadence and keyword targeting.",
            "reason": f"Avg keyword positions improved by {rank_delta:.1f} positions week-over-week.",
        })
    elif rank_delta < -2:
        decisions.append({
            "priority": "high",
            "action": "Keyword positions dropped. Review competing content and refresh underperforming posts.",
            "reason": f"Avg keyword positions dropped by {abs(rank_delta):.1f} week-over-week.",
        })

    bl_delta = seo_trends.get("backlinks_delta", 0)
    if bl_delta == 0 and seo_trends.get("prev_avg_bl", 0) == 0:
        decisions.append({
            "priority": "high",
            "action": "Build backlinks — submit to directories and pursue guest post opportunities.",
            "reason": "Zero backlinks detected. Link acquisition is the highest-leverage SEO activity right now.",
        })

    # Priority actions from seo_daily
    for action in (seo_actions or [])[:2]:
        if not any(action.lower() in d.get("action", "").lower() for d in decisions):
            decisions.append({
                "priority": "medium",
                "action": action,
                "reason": "Identified by daily SEO analysis as a top priority.",
            })

    # Distribution channel decisions
    decisions.append({
        "priority": "ongoing",
        "action": "Continue write.as + Telegraph posting until blocked channels are unblocked.",
        "reason": "Working distribution channel. Track ratio of views per post to gauge platform value.",
    })

    # Competitor-driven decisions
    if competitor_data:
        comp_count = competitor_data.get("competitor_count", 0)
        if comp_count > 0:
            decisions.append({
                "priority": "medium",
                "action": "Leverage competitor comparison pages in content and outreach.",
                "reason": f"Monitoring {comp_count} competitors. Use comparison pages in Reddit/HN comments when accounts are unblocked.",
            })
            # Add competitor stars context
            competitors = competitor_data.get("competitors", {})
            if competitors:
                top_comp = max(competitors.items(), key=lambda x: x[1].get("stars") or 0)
                decisions.append({
                    "priority": "info",
                    "action": f"Note: {top_comp[0]} has {top_comp[1].get('stars', 0)} GitHub stars — lean into Ralph's cost and flexibility advantages.",
                    "reason": "Competitor intelligence for positioning decisions.",
                })

    if not decisions:
        decisions.append({
            "priority": "info",
            "action": "Collect more data before changing the strategy.",
            "reason": "No strong signals yet. Keep running the loop.",
        })

    return decisions


def update_strategy_file(now: datetime, summary: dict, decisions: list[dict], seo_current: dict, seo_trends: dict) -> None:
    """Update STRATEGY.md with a new weekly review section."""
    seo_block = [
        f"**SEO Score:** {seo_current.get('onpage_score', 'unknown')} | Ranked keywords: {seo_current.get('ranked_keywords', '?')} | Backlinks: {seo_current.get('backlinks_approx', '?')} | DR: {seo_current.get('domain_rating', '?')}",
        f"**Trends:** ranks {seo_trends.get('rank_delta', '?')}",
    ]

    lines = [
        f"## Weekly Review — {now.strftime('%Y-%m-%d')}",
        "",
        "### SEO Health",
        *seo_block,
        "",
        "### Content Performance",
    ]
    if summary.get("content_summary"):
        for bucket, stats in summary["content_summary"].items():
            lines.append(f"- {bucket}: {stats['posts']} posts, {stats['views']} total views, {stats['avg_views']} avg views/publish")
    else:
        lines.append("- No measurable posts yet.")

    lines.extend(["", "### Weekly Decisions"])
    for item in decisions:
        lines.append(f"- **[{item['priority'].upper()}]** {item['action']} — {item['reason']}")

    lines.extend(["", "### Priority Actions (from SEO analysis)"])
    for action in (seo_current.get("priority_actions") or ["Collect more data"]):
        lines.append(f"- {action}")

    snapshot = "\n".join(lines)

    existing = load_strategy_content()
    marker = f"## Weekly Review — {now.strftime('%Y-%m-%d')}"
    if marker in existing:
        prefix = existing.split(marker)[0].rstrip()
        new_content = prefix + "\n\n" + snapshot
    else:
        new_content = existing.rstrip() + "\n\n" + snapshot

    STRATEGY_FILE.write_text(new_content, encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def write_seo_insights(seo_current: dict, decisions: list[dict]) -> Path:
    """Write SEO gap insights to a file for generate_content.py to read."""
    insights = {
        'generated_at': datetime.now().isoformat(),
        'priority_keywords': seo_current.get('priority_actions', []),
        'gaps': (seo_current.get('content_gap') or {}).get('gaps', []) if isinstance(seo_current.get('content_gap'), dict) else [],
        'onpage_score': seo_current.get('onpage_score', 'N/A'),
        'ranked_keywords': seo_current.get('ranked_keywords', 0),
        'backlinks_approx': seo_current.get('backlinks_approx', 0),
        'weekly_decisions': [d['action'] for d in decisions if d.get('priority') in ('high', 'medium')],
    }
    path = LOG_DIR / 'seo-insights.json'
    path.write_text(json.dumps(insights, indent=2), encoding='utf-8')
    return path


def competitor_report_is_stale(now: datetime, hours: int = 18) -> bool:
    report = SEO_REPORTS_DIR / f"competitor_analysis_{now.strftime('%Y-%m-%d')}.md"
    if not report.exists():
        return True
    modified = datetime.fromtimestamp(report.stat().st_mtime)
    return (now - modified) > timedelta(hours=hours)


def main() -> int:
    now = datetime.now()
    weekday = now.weekday()
    is_monday = weekday == 0

    # Run seo_daily.py for fresh SEO intelligence
    print("[run.py] Running seo_daily.py...", flush=True)
    seo_current = run_seo_daily()
    seo_error = seo_current.get("error", "")

    # Load SEO history for trend computation
    seo_history = load_seo_trends(days=14)
    seo_trends = compute_trends(seo_current, seo_history)

    # Site health
    site_health = seo_current.get("site_health", {}) if not seo_error else {}
    if seo_error or not site_health:
        site_health = {
            "homepage": http_status("https://ralphworkflow.com"),
            "robots": http_status("https://ralphworkflow.com/robots.txt"),
            "sitemap": http_status("https://ralphworkflow.com/sitemap.xml"),
        }

    # Content performance
    posts = recent_successful_posts(load_posted_records(), now)
    posts = enrich_posts_with_views(posts)
    content_summary = summarize_content_performance(posts)
    totals = {
        "posts_last_30d": len(posts),
        "views_last_30d": sum(int(post.get("views", 0)) for post in posts),
    }

    # Run competitor analysis on Mondays or whenever the report is stale/missing.
    competitor_data = None
    if is_monday or competitor_report_is_stale(now):
        print("[run.py] Running competitor analysis...", flush=True)
        competitor_data = run_competitor_analysis()
        print(f"[run.py] Competitor analysis: {competitor_data.get('competitor_count', 0)} competitors tracked", flush=True)

    adoption_data = load_adoption_data()

    # Decisions should update whenever the primary repo is flat, not only on Mondays.
    decisions = []
    if is_monday or adoption_is_flat(adoption_data):
        decisions = build_weekly_decisions(
            content_summary,
            seo_trends,
            seo_current,
            seo_current.get("priority_actions", []),
            competitor_data=competitor_data,
            adoption_data=adoption_data,
        )
        update_strategy_file(now, {
            "content_summary": content_summary,
            "totals": totals,
        }, decisions, seo_current, seo_trends)

    # Always write SEO insights for generate_content.py
    insights_path = write_seo_insights(seo_current, decisions)
    print(f"[run.py] SEO insights written to {insights_path}", flush=True)

    # Trigger content generation every day so the loop can actually hand work to posting.
    print("[run.py] Triggering content generation from SEO insights...", flush=True)
    generation_result = subprocess.run(
        [sys.executable, str(AGENTS_DIR / "generate_content.py")],
        capture_output=True, text=True, timeout=60,
    )
    generation_stdout = (generation_result.stdout or "").strip()
    generation_stderr = (generation_result.stderr or "").strip()
    if generation_result.returncode == 0:
        print(f"[run.py] Content generation stdout: {generation_stdout[:200]}", flush=True)
    else:
        print(f"[run.py] Content generation warning: {generation_stderr[:200]}", flush=True)

    # Try the posting step after generation so the daily marketing loop can actually publish.
    print("[run.py] Triggering posting step...", flush=True)
    posting_result = subprocess.run(
        [sys.executable, str(AGENTS_DIR / "run_posting.py")],
        capture_output=True, text=True, timeout=120,
    )
    posting_stdout = (posting_result.stdout or "").strip()
    posting_stderr = (posting_result.stderr or "").strip()
    if posting_result.returncode == 0:
        print(f"[run.py] Posting stdout: {posting_stdout[:200]}", flush=True)
    else:
        print(f"[run.py] Posting warning: {posting_stderr[:200]}", flush=True)

    # Build payload
    payload = {
        "timestamp": now.isoformat(),
        "weekly_mode": is_monday,
        "site_health": site_health,
        "totals": totals,
        "content_summary": content_summary,
        "decisions": decisions,
        "blocked_channels": BLOCKED_CHANNELS,
        "seo": {
            "score": seo_current.get("onpage_score", "N/A"),
            "ranked_keywords": sum(1 for v in seo_current.get("ranks", {}).values() if isinstance(v, dict) and v.get("position")),
            "backlinks_approx": seo_current.get("backlinks", {}).get("count_approx", 0) if isinstance(seo_current.get("backlinks"), dict) else seo_current.get("backlinks_approx", 0),
            "domain_rating": seo_current.get("domain_rating"),
            "trends": seo_trends,
            "priority_actions": seo_current.get("priority_actions", []),
            "report": seo_current.get("report", ""),
            "seo_daily_error": seo_error,
            "sitemap_urls": (site_health.get("sitemap") or {}).get("url_count", 0) if isinstance(site_health.get("sitemap"), dict) else 0,
            "competitor_analysis": competitor_data,
        },
        "adoption": adoption_data,
        "failure_signals": [d["action"] for d in decisions if d.get("is_failing_signal")],
        "marketing_status": "failing" if any(d.get("is_failing_signal") for d in decisions) else "mixed" if decisions else "initial",
        "content_generation": {
            "returncode": generation_result.returncode,
            "stdout": generation_stdout,
            "stderr": generation_stderr,
        },
        "posting": {
            "returncode": posting_result.returncode,
            "stdout": posting_stdout,
            "stderr": posting_stderr,
        },
    }

    log_file = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d')}.json"
    log_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
