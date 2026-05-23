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

ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing.distribution_lane_executor import execute_distribution_lane
from agents.marketing.distribution_lane_selector import choose_distribution_lane
from agents.marketing.market_intelligence_runtime import load_market_intelligence

AGENTS_DIR = ROOT / "agents/marketing"
LOG_DIR = AGENTS_DIR / "logs"
STRATEGY_FILE = AGENTS_DIR / "STRATEGY.md"
POSTED_FILE = LOG_DIR / "posted_urls.json"
SEO_REPORTS_DIR = ROOT / "seo-reports"
ADOPTION_FILE = LOG_DIR / "adoption_metrics_latest.json"
MARKET_INTELLIGENCE_FILE = LOG_DIR / "market_intelligence_latest.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BLOCKED_CHANNELS = {
    "dev.to": "Needs API key or OAuth",
    "twitter": "Login/session blocked",
    "reddit": "Account u/Informal-Salt827 verified and active (21 posts published May 16-18). Manual follow-up replies needed to avoid thread necromancy flags.",
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


def load_shared_market_intelligence() -> dict | None:
    """Load the shared competitor/positioning findings artifact when available."""
    return load_market_intelligence("agents/marketing/run.py")


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
            "repair": "Replace current content format/distribution approach. write.as is dead — use only Telegraph. Try: (a) direct repo README/CONTRIBUTING improvement, (b) SEO landing pages targeting repo-specific terms, (c) cross-post to Dev.to when API key is available, all with explicit Codeberg CTAs.",
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
    has_real_content_signal = any((stats.get('views', 0) or 0) > 0 for stats in content_summary.values())
    if ranked and has_real_content_signal:
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
    elif ranked:
        decisions.append({
            "priority": "info",
            "action": "Do not infer a winning owned-content format yet.",
            "reason": "Current content-performance logs show zero measurable views, so format recommendations would be guesswork.",
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
        if adoption_is_flat(adoption_data) and any(token in action.lower() for token in ('create content', 'publish', 'telegraph')):
            continue
        if not any(action.lower() in d.get("action", "").lower() for d in decisions):
            decisions.append({
                "priority": "medium",
                "action": action,
                "reason": "Identified by daily SEO analysis as a top priority.",
            })

    # Distribution channel decisions
    if adoption_is_flat(adoption_data):
        decisions.append({
            "priority": "medium",
            "action": "Hold Telegraph at maintenance only; do not treat more owned-content volume as the next best move.",
            "reason": "Primary repo adoption is flat and owned-content output is already saturated; shift effort to curator, backlink, and comparison distribution lanes.",
        })
        decisions.append({
            "priority": "medium",
            "action": "Ship comparison-led backlink outreach packets whenever the curator queue is already full.",
            "reason": "A follow-through note is not enough when the queue is saturated; the loop needs a fresh executable distribution asset tied to existing comparison pages.",
        })
    else:
        decisions.append({
            "priority": "ongoing",
            "action": "Continue Telegraph posting. write.as is permanently blocked — do not use. Seek Dev.to API key for second platform.",
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

    market_intelligence = load_shared_market_intelligence()
    if competitor_data is None and market_intelligence:
        competitor_data = {
            "report_path": market_intelligence.get("summary_report"),
            "competitor_count": len(market_intelligence.get("competitors", {})),
            "competitors": {
                slug: {
                    "status": data.get("site_status"),
                    "stars": data.get("github_stars"),
                    "features_found": data.get("key_features_found", [])[:3],
                }
                for slug, data in market_intelligence.get("competitors", {}).items()
            },
            "comparison_pages": len(market_intelligence.get("comparison_pages", [])),
            "comparison_pages_list": [item.get("slug") for item in market_intelligence.get("comparison_pages", [])],
            "shared_artifact": str(MARKET_INTELLIGENCE_FILE),
        }

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

    apollo_sequence_status_script = AGENTS_DIR / 'apollo_sequence_status.py'
    if apollo_sequence_status_script.exists():
        subprocess.run(
            [sys.executable, str(apollo_sequence_status_script)],
            capture_output=True,
            text=True,
            timeout=30,
        )

    # --- Repair-awareness gate ---
    # If the audit has pending repairs (repair_state=needs_execution), the crons have
    # kept running the same failing patterns while the repair plan sat unread.
    # Intercept here and apply repair constraints before lane selection + execution.
    AUDIT_PATH = LOG_DIR / "marketing_workflow_audit_latest.json"
    pending_repairs: list[dict] = []
    if AUDIT_PATH.exists():
        try:
            audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            if audit.get("repair_window_status") == "needs_repair":
                pending_repairs = [
                    r for r in audit.get("repair_actions", []) or []
                    if r.get("repair_state") in ("needs_execution", "pending_measurement")
                ]
        except (json.JSONDecodeError, OSError):
            pass

    is_repair_mode = bool(pending_repairs)
    skip_directory_submissions = any(
        r.get("failure_type") == "same_family_distribution_overlap"
        for r in pending_repairs
    )
    skip_curator_outreach = any(
        r.get("failure_type") == "same_family_outreach_overlap"
        for r in pending_repairs
    )
    primary_repo_flat_repair = next(
        (r for r in pending_repairs if r.get("failure_type") == "primary_repo_flat"),
        None
    )
    if is_repair_mode:
        print(f"[run.py] REPAIR MODE active — {len(pending_repairs)} pending repairs, "
              f"skip_dir={skip_directory_submissions}, skip_curator={skip_curator_outreach}", flush=True)
        # Mark repairs as measurement-pending immediately so the next audit sees them
        # as in-flight rather than perpetually needs_execution.
        try:
            audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            for ra in audit.get("repair_actions", []):
                if ra.get("repair_state") == "needs_execution":
                    ra["repair_state"] = "pending_measurement"
                    ra["repair_acknowledged_at"] = now.isoformat()
            AUDIT_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
            print("[run.py] repair_state updated to pending_measurement in audit", flush=True)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[run.py] WARNING: could not update audit repair state: {e}", flush=True)

    distribution_lane = choose_distribution_lane(now)
    # Apply repair constraints to the lane decision so the executor respects them.
    if is_repair_mode:
        # LaneDecision is frozen=True so use __setattr__.
        object.__setattr__(distribution_lane, 'skip_directory_submissions', skip_directory_submissions)
        object.__setattr__(distribution_lane, 'skip_curator_outreach', skip_curator_outreach)
        # When primary_repo_flat repair is active, prefer comparison_backlink_outreach
        # or another lane that creates Codeberg-primary conversion evidence.
        if primary_repo_flat_repair and distribution_lane.lane in (
            "directory_submission", "curator_outreach", "owned_content"
        ):
            # Redirect to a lane that does not depend on owned-content saturation.
            redirect = "comparison_backlink_outreach"
            print(f"[run.py] primary_repo_flat repair active — redirecting from "
                  f"{distribution_lane.lane} to {redirect}", flush=True)
            object.__setattr__(distribution_lane, 'lane', redirect)
            object.__setattr__(distribution_lane, 'reason',
                "Repair override: primary_repo_flat repair active; "
                "pushing Codeberg-primary comparison backlinks instead of saturated patterns."
            )
            distribution_lane.reasons.insert(
                0,
                f"REPAIR: {primary_repo_flat_repair.get('action', primary_repo_flat_repair.get('failure_type', ''))[:120]}"
            )

    # --- Second repair gate: handle pending_measurement repairs ---
    # After the first gate acknowledges repairs, subsequent runs reach here with
    # repair_state=pending_measurement. The lane selector still picks normal lanes
    # (distribution_reset etc.) without knowing about the repair redirect needs.
    # Intercept here and redirect to the repair-appropriate lane.
    pending_measurement_repairs = [
        r for r in pending_repairs
        if r.get("repair_state") == "pending_measurement"
    ]
    if pending_measurement_repairs:
        # Check each pending repair and redirect accordingly.
        for repair in pending_measurement_repairs:
            ft = repair.get("failure_type", "")
            if ft == "primary_repo_flat" and distribution_lane.lane in (
                "directory_submission", "curator_outreach", "owned_content", "distribution_reset"
            ):
                redirect = "comparison_backlink_outreach"
                print(f"[run.py] primary_repo_flat repair (pending_measurement) — redirecting from "
                      f"{distribution_lane.lane} to {redirect}", flush=True)
                object.__setattr__(distribution_lane, 'lane', redirect)
                object.__setattr__(distribution_lane, 'reason',
                    "Repair redirect: primary_repo_flat repair is in measurement window; "
                    "pushing comparison backlinks to create Codeberg-primary conversion evidence.")
                # Remove the redirect repair from list so we don't double-redirect.
                pending_measurement_repairs.remove(repair)
                break
        # Remaining pending repairs: update skip flags.
        for repair in pending_measurement_repairs:
            ft = repair.get("failure_type", "")
            if ft == "same_family_distribution_overlap":
                object.__setattr__(distribution_lane, 'skip_directory_submissions', True)
                print("[run.py] same_family_distribution_overlap repair (pending_measurement) — skip directory submissions", flush=True)
            elif ft == "same_family_outreach_overlap":
                object.__setattr__(distribution_lane, 'skip_curator_outreach', True)
                print("[run.py] same_family_outreach_overlap repair (pending_measurement) — skip curator outreach", flush=True)

    distribution_execution = execute_distribution_lane(distribution_lane, now)
    print(f"[run.py] Chosen distribution lane: {distribution_lane.lane}", flush=True)
    if distribution_execution.artifact_path:
        print(f"[run.py] Distribution execution artifact: {distribution_execution.artifact_path}", flush=True)

    if distribution_lane.lane == "owned_content":
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
    else:
        generation_result = subprocess.CompletedProcess(args=["generate_content.py"], returncode=0, stdout=f"skipped: lane={distribution_lane.lane}", stderr="")
        generation_stdout = generation_result.stdout
        generation_stderr = generation_result.stderr
        posting_result = subprocess.CompletedProcess(args=["run_posting.py"], returncode=0, stdout=f"skipped: lane={distribution_lane.lane}", stderr="")
        posting_stdout = posting_result.stdout
        posting_stderr = posting_result.stderr
        print(f"[run.py] Skipping owned-content generation/posting; using {distribution_lane.lane} lane.", flush=True)

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
        "market_intelligence": market_intelligence,
        "distribution_lane": distribution_lane.__dict__,
        "distribution_execution": distribution_execution.__dict__,
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
