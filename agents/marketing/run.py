#!/usr/bin/env python3
"""Daily metrics collector and weekly evaluator for RalphWorkflow marketing.

Active loop:
- measure only live channels
- compare content buckets using structured metadata
- make small weekly decisions rather than broad speculative plans
"""
from __future__ import annotations

import json
import re
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing")
LOG_DIR = AGENTS_DIR / "logs"
STRATEGY_FILE = AGENTS_DIR / "STRATEGY.md"
POSTED_FILE = LOG_DIR / "posted_urls.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BLOCKED_CHANNELS = {
    "dev.to": "Needs API key or OAuth",
    "twitter": "Login/session blocked",
    "reddit": "Needs account + karma",
    "hackernews": "Needs account",
    "lobsters": "Needs invite/account",
    "producthunt": "Protected flow / manual launch required",
}


def http_status(url: str) -> dict:
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                return {"ok": True, "status": resp.status, "method": method}
        except Exception as exc:  # pragma: no cover - network behavior varies
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
    except Exception:  # pragma: no cover - network behavior varies
        return 0


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


def build_weekly_decisions(content_summary: dict[str, dict], site_health: dict) -> list[dict]:
    decisions: list[dict] = []
    if not site_health["homepage"].get("ok"):
        decisions.append({"priority": "high", "action": "Fix homepage availability before more promotion.", "reason": "Primary site health failed."})

    ranked = sorted(content_summary.items(), key=lambda item: item[1].get("avg_views", 0), reverse=True)
    if ranked:
        best_type, best_stats = ranked[0]
        decisions.append({
            "priority": "medium",
            "action": f"Keep publishing {best_type} content.",
            "reason": f"Best average views so far: {best_stats.get('avg_views', 0)}.",
        })
        if len(ranked) > 1:
            worst_type, worst_stats = ranked[-1]
            if best_stats.get("avg_views", 0) >= max(1.5 * worst_stats.get("avg_views", 0), 1):
                decisions.append({
                    "priority": "medium",
                    "action": f"Shift one future slot away from {worst_type} toward {best_type}.",
                    "reason": f"{best_type} is outperforming {worst_type} on average views.",
                })
    else:
        decisions.append({"priority": "info", "action": "Collect more data before changing the content mix.", "reason": "No successful posts with measurable views yet."})

    decisions.append({"priority": "ongoing", "action": "Stay focused on write.as + site SEO until blocked channels are unblocked.", "reason": "Current working distribution channel is write.as."})
    decisions.append({"priority": "ongoing", "action": "Track blocked channels separately instead of trying to automate them now.", "reason": ", ".join(sorted(BLOCKED_CHANNELS))})
    return decisions


def render_strategy_snapshot(now: datetime, summary: dict, decisions: list[dict]) -> str:
    lines = [f"## Automation Review — {now.strftime('%Y-%m-%d')}", "", "### Active Loop", "- Generate scheduled RalphWorkflow drafts", "- Publish only real drafts to write.as", "- Measure views + site health", "- Adjust content mix weekly", "", "### Site Health"]
    for name, status in summary["site_health"].items():
        badge = "✅" if status.get("ok") else "❌"
        detail = status.get("status", status.get("error", "unknown"))
        lines.append(f"- {badge} {name}: {detail}")
    lines.extend(["", "### Content Performance"])
    if summary["content_summary"]:
        for bucket, stats in summary["content_summary"].items():
            lines.append(f"- {bucket}: {stats['posts']} posts, {stats['views']} total views, {stats['avg_views']} avg views")
    else:
        lines.append("- No successful posts with measurable data yet")
    lines.extend(["", "### Weekly Decisions"])
    for item in decisions:
        lines.append(f"- [{item['priority'].upper()}] {item['action']} — {item['reason']}")
    return "\n".join(lines) + "\n"


def update_strategy_file(now: datetime, summary: dict, decisions: list[dict]) -> None:
    snapshot = render_strategy_snapshot(now, summary, decisions)
    existing = STRATEGY_FILE.read_text(encoding="utf-8") if STRATEGY_FILE.exists() else "# Ralph Workflow Marketing Strategy\n"
    marker = f"## Automation Review — {now.strftime('%Y-%m-%d')}"
    if marker in existing:
        prefix = existing.split(marker)[0].rstrip()
        new_content = prefix + "\n\n" + snapshot
    else:
        new_content = existing.rstrip() + "\n\n" + snapshot
    STRATEGY_FILE.write_text(new_content, encoding="utf-8")


def build_summary(now: datetime) -> dict:
    site_health = {
        "homepage": http_status("https://ralphworkflow.com"),
        "robots": http_status("https://ralphworkflow.com/robots.txt"),
        "sitemap": http_status("https://ralphworkflow.com/sitemap.xml"),
    }
    posts = recent_successful_posts(load_posted_records(), now)
    posts = enrich_posts_with_views(posts)
    content_summary = summarize_content_performance(posts)
    totals = {
        "posts_last_30d": len(posts),
        "views_last_30d": sum(int(post.get("views", 0)) for post in posts),
    }
    return {
        "timestamp": now.isoformat(),
        "site_health": site_health,
        "posts": posts,
        "content_summary": content_summary,
        "totals": totals,
        "blocked_channels": BLOCKED_CHANNELS,
    }


def main() -> int:
    now = datetime.now()
    summary = build_summary(now)
    weekly_mode = now.weekday() == 0
    decisions = build_weekly_decisions(summary["content_summary"], summary["site_health"]) if weekly_mode else []
    if weekly_mode:
        update_strategy_file(now, summary, decisions)

    payload = {
        "timestamp": now.isoformat(),
        "weekly_mode": weekly_mode,
        "site_health": summary["site_health"],
        "totals": summary["totals"],
        "content_summary": summary["content_summary"],
        "decisions": decisions,
        "blocked_channels": summary["blocked_channels"],
    }
    log_file = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d')}.json"
    log_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
