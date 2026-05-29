#!/usr/bin/env python3
"""ARCHITECTURALLY RETIRED 2026-05-28."""
from __future__ import annotations

import json
import re
import sys

if __name__ == '__main__':
    print(json.dumps({'status': 'retired', 'reason': 'Reddit pipeline architecturally retired 2026-05-28'}))
    sys.exit(0)
import subprocess
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from collections import Counter


ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing.positioning import validate_marketing_copy

REPORT_DIR = ROOT / "seo-reports"
OUT_DIR = ROOT / "drafts"
LOG_PATH = ROOT / "outreach-log.md"
POSTER = ROOT / "agents/marketing/reddit_post.py"
PRAW_POSTER = ROOT / "agents/marketing/reddit_praw_reply.py"


def _praw_available() -> bool:
    """Check if PRAW credentials are filled in TOOLS.md (not just the placeholder template)."""
    try:
        import praw
        tools_text = (ROOT / "TOOLS.md").read_text(encoding="utf-8")
        m = re.search(
            r"^### Reddit API \(PRAW\)\n(?P<section>.*?)(?=^### |\Z)",
            tools_text,
            re.M | re.S,
        )
        if not m:
            return False
        section = m.group("section")
        client_id_m = re.search(r"\*\*Client ID:\*\*\s*(?:<([^>]+)>|([^\n]+))", section)
        client_secret_m = re.search(r"\*\*Client Secret:\*\*\s*(?:<([^>]+)>|([^\n]+))", section)
        if not client_id_m or not client_secret_m:
            return False
        client_id = (client_id_m.group(1) or client_id_m.group(2) or "").strip()
        client_secret = (client_secret_m.group(1) or client_secret_m.group(2) or "").strip()
        if not client_id or not client_secret:
            return False
        lowered = {client_id.lower(), client_secret.lower()}
        placeholders = {
            "<paste from reddit.com/prefs/apps>",
            "paste from reddit.com/prefs/apps",
            "<paste from above>",
            "paste from above",
        }
        if lowered & placeholders:
            return False
        return True
    except Exception:
        return False
LOCK_PATH = ROOT / "agents/marketing/logs/reddit_autopost.lock"
STATE_PATH = ROOT / "agents/marketing/logs/reddit_autopost_state.json"
RETRO_JSON = ROOT / "agents/marketing/logs/reddit_post_analysis.json"
AUDIT_JSON = ROOT / "agents/marketing/logs/marketing_workflow_audit_latest.json"
RETRO_SCRIPT = ROOT / "agents/marketing/reddit_retrospective.py"
POST_LOG_JSONL = ROOT / "agents/marketing/logs/reddit_posts.jsonl"
MARKETING_LOG_DIR = ROOT / "agents/marketing/logs"
# Primary adoption surface is Codeberg (9 stars, 2 forks). GitHub is a secondary mirror (0 stars).
CODEBERG_PRIMARY_URL = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
CODEBERG_REVIEW_PROOF_URL = "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/review-ai-coding-output-before-merge.md"
GITHUB_MIRROR_URL = "https://github.com/Ralph-Workflow/Ralph-Workflow"

BANNED_OPENING_PREFIXES = (
    "i've had the best results when i stop optimizing for more agents",
    "i've had better results when i stop asking whether to trust the agent",
    # 2026-05-19: explicitly banned after REDDIT_LEARNINGS.md update
    "honestly the part i'd optimize first is the handoff",
    # 2026-05-19: audit flag — specific verbatim opening used across multiple subreddits
    "honestly the part i'd optimize first is the handoff, not the model stack",
    # 2026-05-19: repeated verbatim across posts 2 and 3 — also covers near-variants
    "my default is to optimize for a clean morning-after review",
    "the best improvement i've seen is making the output easier to judge",
    "what i kept getting wrong early on was treating 'the agent said it was done'",
    "the part that bites me most is not choosing which tool",
    "the overnight run problem is usually not the agent",
    "switching between claude code and codex sounds like a workflow upgrade",
    # 2026-05-20: additional rephrasings confirmed in reddit_post.py audit
    "the real bottleneck is never the tool switch",
    "switching between claude code and codex sounds like a workflow upgrade",
    "the problem with multi-hop claude workflows is not the model intelligence",
    "what i wanted from a claude plus codex setup was not two opinions",
    "if i had to optimize one thing, it would be the handoff",
    "the handoff is where most overnight runs actually fail",
    # 2026-05-20: banned CTA phrases (not openings but prevent stale product framing)
    "ralph workflow is free and open-source: it orchestrates the handoff between tools",
    "ralph workflow is free and open-source: it runs the ai coding tools you already use",
    "ralph workflow is free and open-source: it adds that discipline to the agents you already use",
)
BANNED_PHRASES = (
    "reviewable work units",
    "for me the reliable pattern is",
    "for me the reliable version is",
    # 2026-05-19: repeated sentence patterns from recent posts
    "if the run ends with a readable diff, checks, and unresolved decisions called out",
    "if the run ends with one readable diff, real checks, and a short note about what still looks sketchy",
    "the run ends with a confident summary",
    "most of the pain is not raw generation",
    "stale assumptions",
    "lying to yourself about the result",
    # 2026-05-20: additional body cadence repeats confirmed in reddit_post.py audit
    "what changed, what ran, and what still needs a human decision",
    "what changed, what ran, and what still looks risky",
    "one readable diff, real checks, and a short note",
    "bounded diff, check results, and a short unresolved list",
    "the morning-after review into a bounded check",
    "transcript archaeology",
    "ralph workflow is free and open-source:",
)

SITE_LANGUAGE_TERMS = (
    "finished code",
    "tested code",
    "ready to review",
    "what changed",
    "would you merge it?",
    "no babysitting",
)

STALE_REDDIT_FRAMING_TERMS = (
    "handoff",
    "baton pass",
    "diff",
    "checks",
    "review surface",
    "reviewable",
)

REVIEW_TAX_TERMS = (
    "review tax",
    "cleanup",
    "reconstruct",
    "reconstruction",
    "verification delay",
    "review drag",
    "bounded run",
    "bounded diff",
    "approval fatigue",
    "approval drag",
    "merge safely",
    "would you merge it",
    "ready to review",
)

GLOBAL_COOLDOWN_MINUTES = 45
COMMUNITY_COOLDOWN_HOURS = 6
MAX_POSTS_PER_6H = 3

# 2026-05-20: persistent body-dedup cache — survives async jsonl writes between
# rapid watchdog/autopost calls so the same body cannot be selected twice in a row
# even when the second call runs before the first post has been logged.
BODY_CACHE_PATH = ROOT / "agents/marketing/logs/reddit_body_cache.json"
BODY_CACHE_TTL_HOURS = 48


def _body_hash(body: str) -> str:
    import hashlib
    return hashlib.sha1(body.encode()).hexdigest()[:16]


def load_body_cache() -> dict[str, float]:
    if not BODY_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(BODY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_body_cache(cache: dict[str, float]) -> None:
    BODY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BODY_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")


def load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_logged_reddit_url(url: str) -> str:
    value = (url or "").strip().strip("<>").replace("https://www.reddit.com/", "https://old.reddit.com/")
    if value and not value.endswith("/"):
        value += "/"
    return value


def _refresh_shared_marketing_state() -> None:
    return None


def _load_report_opportunity_map(report_path: str) -> dict[str, Opportunity]:
    path = Path(report_path)
    if not report_path or not path.exists():
        return {}
    try:
        opps = parse_opportunities(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {_normalize_logged_reddit_url(opp.url): opp for opp in opps}


def _existing_reddit_comment_urls() -> set[str]:
    urls: set[str] = set()
    if not MARKETING_LOG_DIR.exists():
        return urls
    for path in MARKETING_LOG_DIR.glob("marketing_*_reddit_comment_published.json"):
        payload = load_json_file(path)
        for candidate in (
            ((payload.get("chosen_action") or {}).get("url")),
            ((payload.get("chosen_action") or {}).get("thread_url")),
            ((payload.get("result") or {}).get("comment_url")),
            ((payload.get("result") or {}).get("thread_url")),
        ):
            if candidate:
                urls.add(_normalize_logged_reddit_url(str(candidate)))
    return urls


def sync_latest_reddit_post_into_marketing_logs() -> Path | None:
    if not POST_LOG_JSONL.exists():
        return None
    existing_urls = _existing_reddit_comment_urls()
    rows: list[dict] = []
    for line in POST_LOG_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if not rows:
        return None
    rows.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)
    for row in rows:
        thread_url = _normalize_logged_reddit_url(str(row.get("thread_url") or ""))
        comment_url = _normalize_logged_reddit_url(str(row.get("comment_url") or ""))
        if thread_url in existing_urls or comment_url in existing_urls:
            continue
        metadata = dict(row.get("metadata") or {})
        report_map = _load_report_opportunity_map(str(metadata.get("report") or ""))
        report_match = report_map.get(thread_url)
        if report_match is not None:
            metadata.setdefault("title", report_match.title)
            metadata.setdefault("community", report_match.community)
            metadata.setdefault("angle", report_match.angle)
            metadata.setdefault("mention_fit", report_match.mention_fit)
            metadata.setdefault("direct_reply_fit", report_match.direct_reply_fit)
        title = str(metadata.get("title") or row.get("note") or "Reddit thread")
        community = str(metadata.get("community") or "")
        angle = str(metadata.get("angle") or "")
        mention_fit = str(metadata.get("mention_fit") or "")
        timestamp = str(row.get("timestamp") or datetime.now().isoformat())
        safe_stamp = re.sub(r"[^0-9]", "", timestamp)[:15] or datetime.now().strftime("%Y%m%d%H%M%S")
        MARKETING_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = MARKETING_LOG_DIR / f"marketing_{safe_stamp}_reddit_comment_published.json"
        payload = {
            "timestamp": timestamp,
            "chosen_action": {
                "type": "reddit_comment_published",
                "title": f"Reddit comment published: {title}",
                "url": comment_url or thread_url,
                "thread_url": thread_url,
            },
            "why_this_action": {
                "summary": "Backfilled published Reddit comment into marketing logs.",
                "supporting_reasons": [
                    f"community: {community}" if community else "community: unknown",
                    f"mention_fit: {mention_fit}" if mention_fit else "mention_fit: unknown",
                    f"angle: {angle}" if angle else "angle: unknown",
                ],
            },
            "result": {
                "status": "published",
                "live_external_action": True,
                "thread_url": thread_url,
                "comment_url": comment_url or thread_url,
                "body": row.get("body"),
            },
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _refresh_shared_marketing_state()
        return path
    return None


def recent_body_hashes(limit: int = 8) -> set[str]:
    """Return SHA1 prefixes of bodies posted in the last BODY_CACHE_TTL_HOURS."""
    cache = load_body_cache()
    now = __import__("time").time()
    active = {
        h: ts for h, ts in cache.items()
        if (now - ts) < (BODY_CACHE_TTL_HOURS * 3600)
    }
    if active != cache:
        save_body_cache(active)
    return set(sorted(active.keys(), key=lambda h: active[h], reverse=True)[:limit])


def mark_body_used(body: str) -> None:
    """Record a body hash immediately after a successful post."""
    cache = load_body_cache()
    cache[_body_hash(body)] = __import__("time").time()
    save_body_cache(cache)


def body_is_recently_used(body: str) -> bool:
    return _body_hash(body) in recent_body_hashes()


@dataclass
class Opportunity:
    rank: int
    title: str
    url: str
    community: str
    angle: str
    freshness: str
    mention_fit: str = ""
    direct_reply_fit: str = ""


def latest_report() -> Path:
    reports = []
    for report in REPORT_DIR.glob("reddit_monitor_*.md"):
        stem = report.stem
        try:
            datetime.strptime(stem[len("reddit_monitor_"):], "%Y-%m-%d_%H%M")
        except ValueError:
            continue
        reports.append(report)
    reports.sort()
    if not reports:
        raise SystemExit("No reddit_monitor report found.")
    return reports[-1]


def parse_opportunities(report_text: str) -> list[Opportunity]:
    sections = re.split(r"(?m)^###\s+(?=\d+\))", report_text)
    found: list[Opportunity] = []

    for section in sections[1:]:
        lines = section.strip().splitlines()
        if not lines:
            continue

        header = lines[0].strip()
        header_match = re.match(r"(\d+)\)\s+(.+)", header)
        if not header_match:
            continue

        rank = int(header_match.group(1))
        title = header_match.group(2).strip()

        def extract(pattern: str) -> str:
            match = re.search(pattern, section, re.M)
            return match.group(1).strip() if match else ""

        url = extract(r"^- URL:\s*(.+)$")
        community = extract(r"^- Community:\s*(.+)$")
        freshness = extract(r"^- Freshness:\s*(.+)$")
        mention_fit = extract(r"^- Mention fit:\s*(.+)$")
        direct_reply_fit = extract(r"^- Direct reply fit:\s*(.+)$")

        angle = ""
        inline_angle = extract(r"^- (?:Best RalphWorkflow angle|Recommended angle):\s*(.+)$")
        if inline_angle:
            angle = inline_angle
        else:
            angle_match = re.search(
                r"^- (?:Best RalphWorkflow angle|Recommended angle):\s*\n\s*-\s+(.+?)(?=\n- [A-Z]|\n###|\Z)",
                section,
                re.M | re.S,
            )
            if angle_match:
                angle = angle_match.group(1).strip()

        angle = " ".join(angle.split())
        angle = re.sub(r"^[-*]\s*", "", angle).strip()
        if angle.startswith("**") and angle.endswith("**") and len(angle) > 4:
            angle = angle[2:-2].strip()

        if not freshness:
            # Newer monitor reports sometimes omit a dedicated freshness line after
            # already filtering for live/current threads. Keep those actionable.
            freshness = "during this pass"

        if not (url and community and angle):
            continue

        found.append(
            Opportunity(
                rank,
                title,
                url,
                community,
                angle,
                " ".join(freshness.split()),
                " ".join(mention_fit.split()),
                " ".join(direct_reply_fit.split()),
            )
        )

    return found


def already_used(url: str) -> bool:
    if not LOG_PATH.exists():
        return False
    text = LOG_PATH.read_text(encoding="utf-8")
    old = url.replace("https://www.reddit.com/", "https://old.reddit.com/")
    return url in text or old in text


def normalize_community(value: str) -> str:
    return value.strip().strip("`").lower()


def normalize_dt(value: datetime) -> datetime:
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def load_recent_post_records(hours: int = 24) -> list[dict]:
    if not POST_LOG_JSONL.exists():
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    rows = []
    for line in POST_LOG_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        ts_raw = row.get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        ts = normalize_dt(ts)
        if ts >= cutoff:
            row["__parsed_timestamp"] = ts
            rows.append(row)
    return rows


def iso_local(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def posting_gate(now: datetime, recent_posts: list[dict]) -> tuple[bool, str | None, int | None, str | None]:
    if not recent_posts:
        return True, None, None, None
    recent_posts = sorted(recent_posts, key=lambda row: row.get("__parsed_timestamp") or datetime.min)
    latest = recent_posts[-1].get("__parsed_timestamp")
    if latest and now - latest < timedelta(minutes=GLOBAL_COOLDOWN_MINUTES):
        mins = int((now - latest).total_seconds() // 60)
        next_safe = latest + timedelta(minutes=GLOBAL_COOLDOWN_MINUTES)
        retry_after = max(1, int((next_safe - now).total_seconds() // 60))
        return False, f"global_cooldown_active:{mins}m_since_last_post", retry_after, iso_local(next_safe)
    posts_last_6h = [row for row in recent_posts if now - row.get("__parsed_timestamp") <= timedelta(hours=6)]
    if len(posts_last_6h) >= MAX_POSTS_PER_6H:
        oldest_in_window = sorted(posts_last_6h, key=lambda row: row.get("__parsed_timestamp") or datetime.min)[0].get("__parsed_timestamp")
        next_safe = oldest_in_window + timedelta(hours=6, minutes=1) if oldest_in_window else None
        retry_after = max(1, int((next_safe - now).total_seconds() // 60)) if next_safe else None
        return False, f"volume_guard_active:{len(posts_last_6h)}_posts_in_6h", retry_after, iso_local(next_safe)
    return True, None, None, None


def community_recently_used(community: str, now: datetime, recent_posts: list[dict]) -> bool:
    wanted = normalize_community(community)
    for row in recent_posts:
        ts = row.get("__parsed_timestamp")
        meta = row.get("metadata") or {}
        seen = normalize_community(str(meta.get("community") or ""))
        if seen == wanted and ts and now - ts <= timedelta(hours=COMMUNITY_COOLDOWN_HOURS):
            return True
    return False


def mention_fit_score(mention_fit: str) -> int:
    fit = mention_fit.strip().strip("*").lower()
    if fit == "high":
        return 4
    if fit == "medium-high":
        return 3
    if fit == "medium":
        return 2
    if fit == "medium-low":
        return 1
    return 0


def finish_surface_score(opp: Opportunity) -> int:
    category = detect_category(opp.title)
    text = f"{opp.title}\n{opp.angle}".lower()
    review_tax_hits = sum(1 for term in REVIEW_TAX_TERMS if term in text)
    if review_tax_hits >= 2:
        return 3
    if review_tax_hits == 1:
        return 2
    if category in {"trust", "handoff", "mixed_team", "breaks_first", "workflow", "overnight", "remote", "approval"}:
        return 1
    return 0


def report_posting_guard(report_text: str, opps: list[Opportunity]) -> list[str]:
    reasons: list[str] = []
    lowered = report_text.lower()
    if "important telemetry note" in lowered:
        reasons.append("report_coverage_unhealthy")
    if "partial coverage" in lowered or "reddit_ip_blocked=" in lowered:
        reasons.append("report_partial_coverage")
    if opps and max((mention_fit_score(opp.mention_fit) for opp in opps), default=0) < 2:
        reasons.append("mention_fit_below_medium")
    return reasons


def choose_opportunity(opps: list[Opportunity]) -> tuple[Opportunity | None, str]:
    now = datetime.now()
    recent_posts = load_recent_post_records(hours=24)

    def unused_with_min_score(min_score: int, *, respect_community_cooldown: bool, min_fit: int = 0) -> list[Opportunity]:
        return [
            opp for opp in opps
            if not already_used(opp.url)
            and freshness_score(opp.freshness) >= min_score
            and mention_fit_score(opp.mention_fit) >= min_fit
            and (
                not respect_community_cooldown
                or not community_recently_used(opp.community, now, recent_posts)
            )
        ]

    def rank_candidates(candidates: list[Opportunity]) -> list[Opportunity]:
        return sorted(
            candidates,
            key=lambda opp: (
                -mention_fit_score(opp.mention_fit),
                -finish_surface_score(opp),
                -freshness_score(opp.freshness),
                opp.rank,
            ),
        )

    same_day_unused = unused_with_min_score(5, respect_community_cooldown=True, min_fit=2)
    if same_day_unused:
        return rank_candidates(same_day_unused)[0], "fresh"

    same_day_rate_limited = unused_with_min_score(5, respect_community_cooldown=False, min_fit=2)
    if same_day_rate_limited:
        return None, "fresh_rate_limited"

    fresh_unused = unused_with_min_score(4, respect_community_cooldown=True, min_fit=2)
    if fresh_unused:
        return rank_candidates(fresh_unused)[0], "fresh"

    fresh_but_rate_limited = unused_with_min_score(4, respect_community_cooldown=False, min_fit=2)
    if fresh_but_rate_limited:
        return None, "fresh_rate_limited"

    medium_low_unused = [
        opp for opp in opps
        if not already_used(opp.url)
        and freshness_score(opp.freshness) >= 4
        and mention_fit_score(opp.mention_fit) == 1
    ]
    if medium_low_unused:
        return None, "weak_fit_only"

    weak_fit_unused = [opp for opp in opps if not already_used(opp.url) and mention_fit_score(opp.mention_fit) == 0]
    if weak_fit_unused:
        return None, "weak_fit_only"

    stale_unused = [opp for opp in opps if not already_used(opp.url)]
    if stale_unused:
        return None, "stale_only"

    return None, "fully_consumed"


def freshness_score(freshness: str, reference: datetime | None = None) -> int:
    text = freshness.strip().lower()
    if not text:
        return 0

    reference = reference or datetime.now()

    match = re.search(r"([a-z]+)\s+(\d{1,2}),\s+(\d{4})", text)
    if match:
        try:
            parsed = datetime.strptime(match.group(0).title(), "%B %d, %Y")
            delta = (reference.date() - parsed.date()).days
            if delta <= 1:
                return 5
            if delta <= 7:
                return 4
            if delta <= 14:
                return 3
            if delta <= 30:
                return 2
            return 1
        except ValueError:
            pass

    if any(token in text for token in [
        "active same-day",
        "same-day",
        "today",
        "hours ago",
        "hour ago",
        "this pass",
        "during this pass",
    ]):
        return 5
    if "yesterday" in text:
        return 4
    if "last week" in text or "this week" in text:
        return 3
    if "late april" in text or "early" in text:
        return 1
    if any(day in text for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]):
        return 3
    return 2 if "april" not in text else 1


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def refresh_retrospective() -> dict:
    subprocess.run([sys.executable, str(RETRO_SCRIPT)], capture_output=True, text=True)
    return load_json_file(RETRO_JSON)


def reddit_lane_repair_reasons(audit: dict | None = None, retro: dict | None = None) -> list[str]:
    audit = audit or load_json_file(AUDIT_JSON)
    retro = retro or load_json_file(RETRO_JSON)

    reasons: list[str] = []
    failing_tactics = {str(item) for item in (audit.get("failing_tactics") or [])}
    if "reddit_style_repetition" in failing_tactics:
        reasons.append("audit:failing_tactic:reddit_style_repetition")

    for action in audit.get("repair_actions") or []:
        repair_state = str(action.get("repair_state") or "")
        target_tactic = str(action.get("target_tactic") or "")
        failure_type = str(action.get("failure_type") or "")
        if repair_state != "needs_execution":
            continue
        if target_tactic == "reddit_post_style" or failure_type == "repetitive_outreach":
            reasons.append("audit:repair_pending:reddit_post_style")
            break

    repeated_openings = retro.get("repeated_openings") or []
    if repeated_openings:
        reasons.append(f"retro:repeated_openings:{len(repeated_openings)}")

    recent_window_count = int(retro.get("recent_window_count", 0) or 0)
    by_community = retro.get("by_community") or {}
    claude_code_count = int(by_community.get("r/ClaudeCode", 0) or 0)
    if recent_window_count >= 5 and claude_code_count >= 5:
        reasons.append(
            f"retro:channel_concentration:r/ClaudeCode:{claude_code_count}/{recent_window_count}"
        )

    return reasons


def recent_bodies(limit: int = 8) -> list[str]:
    log_path = ROOT / "agents/marketing/logs/reddit_posts.jsonl"
    if not log_path.exists():
        return []
    rows = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return [str(r.get("body") or "") for r in rows[-limit:]]


def opening_is_repetitive(body: str, recent: list[str]) -> bool:
    opening = (body.splitlines()[0].strip().lower() if body.splitlines() else "")
    if not opening:
        return False
    if any(opening.startswith(prefix) for prefix in BANNED_OPENING_PREFIXES):
        return True
    # 2026-05-20 fix: also check recent_body_hashes cache — bodies posted the same
    # day are in the cache (via mark_body_used) but may not yet be visible in the
    # jsonl when a second same-day post runs build_comment before the jsonl write
    # from the first post propagates. Catching by hash closes the same-day gap.
    if body_is_recently_used(body):
        return True
    recent_openings = {(r.splitlines()[0].strip().lower() if r.splitlines() else "") for r in recent}
    return opening in recent_openings


def opening_family(body: str) -> str:
    opening = (body.splitlines()[0].strip().lower() if body.splitlines() else "")
    if not opening:
        return "none"
    if any(token in opening for token in ["approval mode", "plan mode", "approval-heavy", "click approve", "trustworthy stop point", "live safety system"]):
        return "approval_drag"
    if any(token in opening for token in ["run until done", "autonomy features", "stop cleanly", "stop condition"]):
        return "stop_condition"
    if any(token in opening for token in ["remote access", "from your phone", "remote"]):
        return "remote_supervision"
    if any(token in opening for token in ["overnight drift", "overnight", "morning"]):
        return "overnight_scope"
    if any(token in opening for token in ["trust", "confidence", "final authority"]):
        return "trust_boundary"
    if any(token in opening for token in ["merge", "merged state", "shared boundary", "review overhead"]):
        return "merge_safety"
    if any(token in opening for token in ["agent teams", "mixed-agent", "permission mismatch", "specialists fan out"]):
        return "mixed_team_state"
    if any(token in opening for token in ["claude", "codex", "handoff", "baton pass", "role split", "one tool writes"]):
        return "handoff_contract"
    if any(token in opening for token in ["output easier to judge", "clean morning-after review", "audit"]):
        return "review_surface"
    # 2026-05-20: new families for fresh body patterns
    if any(token in opening for token in ["the question i now ask", "what i ask myself", "the question i always ask", "the first thing i check", "the test i run"]):
        return "interrogative_stance"
    if any(token in opening for token in ["the approval fatigue", "the midnight babysitting", "context switch", "interruption", "real cost"]):
        return "cost_pain"
    if any(token in opening for token in ["only worth it if", "only useful if", "only makes sense if", "only works if", "only true when"]):
        return "conditional_frame"
    if opening.startswith(("does the run", "does it end", "is the result", "was it worth", "did it actually")):
        return "formula_question"
    return "generic"


def opening_family_repeats(body: str, recent: list[str]) -> bool:
    family = opening_family(body)
    if family in {"none", "generic"}:
        return False
    recent_families = {opening_family(prev) for prev in recent if prev.strip()}
    return family in recent_families


VERBATIM_DUPLICATE_BODY = (
    "honestly the part i'd optimize first is the handoff, not the model stack.\n\n"
    "if the run ends with one readable diff, real checks, and a short note about what still looks sketchy, "
    "you can move fast without lying to yourself about the result.\n\n"
    "most of the pain is not raw generation. it's stale assumptions, fuzzy ownership, "
    "and nobody making the finish easy to review."
)


def contains_banned_phrase(body: str) -> bool:
    text = body.lower()
    if VERBATIM_DUPLICATE_BODY in text:
        return True
    return any(phrase in text for phrase in BANNED_PHRASES)


def body_similarity(body: str, recent: list[str]) -> float:
    tokens = re.findall(r"[a-z0-9']+", body.lower())
    if not tokens or not recent:
        return 0.0
    body_counts = Counter(tokens)
    best = 0.0
    for prev in recent:
        prev_tokens = re.findall(r"[a-z0-9']+", prev.lower())
        if not prev_tokens:
            continue
        prev_counts = Counter(prev_tokens)
        overlap = sum((body_counts & prev_counts).values())
        denom = max(sum(body_counts.values()), sum(prev_counts.values())) or 1
        best = max(best, overlap / denom)
    return best


def paragraph_count(body: str) -> int:
    return len([p for p in body.split("\n\n") if p.strip()])


def has_bullet_list(body: str) -> bool:
    return "\n- " in body


def closing_slot_repeats(body: str, recent: list[str]) -> bool:
    final_para = [p.strip().lower() for p in body.split("\n\n") if p.strip()]
    if not final_para:
        return False
    final_para = final_para[-1]
    if "ralphworkflow" not in final_para and "ralph workflow" not in final_para:
        return False
    repeated = 0
    for prev in recent:
        parts = [p.strip().lower() for p in prev.split("\n\n") if p.strip()]
        if parts and ("ralphworkflow" in parts[-1] or "ralph workflow" in parts[-1]):
            repeated += 1
    return repeated >= 2


def github_cta_repeats(body: str, recent: list[str]) -> bool:
    # 2026-05-20: fix -- was checking GITHUB_MIRROR_URL, should check CODEBERG_PRIMARY_URL
    # since snippets now use Codeberg as primary. GitHub is the mirror and should not
    # appear as the primary CTA in Reddit posts.
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    current_cta = next((p.lower() for p in paragraphs if CODEBERG_PRIMARY_URL in p), "")
    if not current_cta:
        return False
    repeated = 0
    for prev in recent:
        prev_paragraphs = [p.strip().lower() for p in prev.split("\n\n") if p.strip()]
        prev_cta = next((p for p in prev_paragraphs if CODEBERG_PRIMARY_URL in p), "")
        if prev_cta and prev_cta == current_cta:
            repeated += 1
    return repeated >= 1


def structure_similarity(body: str, recent: list[str]) -> bool:
    current = (paragraph_count(body), has_bullet_list(body))
    recent_shapes = {(paragraph_count(prev), has_bullet_list(prev)) for prev in recent if prev.strip()}
    return current in recent_shapes


def paragraph_concept(paragraph: str) -> str:
    text = paragraph.lower()
    if "https://github.com/ralph-workflow/ralph-workflow" in text or "ralphworkflow" in text or "ralph workflow" in text:
        return "product_cta"
    if any(token in text for token in ["what breaks first", "confidence in the merged state", "merged state", "operating posture", "trust isn't", "trust as", "failure mode is trust"]):
        return "thesis"
    if any(token in text for token in ["one tool writes", "one tool implements", "one phase owns", "phase", "handoff", "builder", "review pass", "role split"]):
        return "phase_split"
    if any(token in text for token in ["shared boundaries", "shared boundary", "config/schema/migrations", "merged state", "global check"]):
        return "shared_boundary"
    if any(token in text for token in ["finish receipt", "receipt", "morning-after", "re-entry", "heroic transcript", "long transcript"]):
        return "finish_receipt"
    if any(token in text for token in ["checks", "diff", "done criteria", "acceptance criteria", "open questions", "human decision"]):
        return "review_proof"
    return "generic"


def concept_cadence_signature(body: str) -> tuple[str, ...]:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    signature: list[str] = []
    for paragraph in paragraphs:
        concept = paragraph_concept(paragraph)
        if not signature or signature[-1] != concept:
            signature.append(concept)
    return tuple(signature)


def concept_cadence_repeats(body: str, recent: list[str]) -> bool:
    current = concept_cadence_signature(body)
    if not current:
        return False
    recent_signatures = [concept_cadence_signature(prev) for prev in recent if prev.strip()]
    if current in recent_signatures:
        return True
    current_core = tuple(part for part in current if part != "generic")
    for prev in recent_signatures:
        prev_core = tuple(part for part in prev if part != "generic")
        if current_core and current_core == prev_core:
            return True
        if len(current_core) >= 3 and len(prev_core) >= 3 and current_core[:3] == prev_core[:3]:
            return True
    return False


def body_needs_regeneration(body: str, recent: list[str]) -> bool:
    return (
        opening_is_repetitive(body, recent)
        or opening_family_repeats(body, recent)
        or contains_banned_phrase(body)
        or body_similarity(body, recent) > 0.60  # 2026-05-20: lowered from 0.72 to catch near-duplicate bodies
        or structure_similarity(body, recent)
        or closing_slot_repeats(body, recent)
        or github_cta_repeats(body, recent)
        or concept_cadence_repeats(body, recent)
        or sentence_overlap_penalty(body, recent) > 0.55  # 2026-05-20: integrate sentence-level overlap check
    )


def detect_category(title: str) -> str:
    text = title.lower()
    if "trust" in text:
        return "trust"
    if "approval" in text or "plan mode" in text or "approval loop" in text:
        return "approval"
    if any(token in text for token in ["cleanup", "reconstruct", "reconstruction", "review tax", "critique"]):
        return "workflow"
    if "run until done" in text or "/goal" in text:
        return "announcement"
    if "mobile" in text or "remote" in text:
        return "remote"
    if "overnight" in text or "rails" in text or "productive" in text or "autonomous" in text:
        return "overnight"
    if "->" in text or "anyone else" in text:
        return "handoff"
    if "agent teams" in text or "gemini" in text or "team" in text:
        return "mixed_team"
    if "breaks first" in text:
        return "breaks_first"
    if "merge" in text or "safety" in text or "workflow" in text or "critique" in text:
        return "workflow"
    if "codex" in text:
        return "codex"
    return "generic"


def is_high_fit(opp: Opportunity) -> bool:
    fit = (opp.mention_fit or "").lower()
    return "high" in fit


def should_add_github_link(opp: Opportunity) -> bool:
    """Whether to add a RalphWorkflow product mention with Codeberg primary CTA."""
    community = normalize_community(opp.community)
    category = detect_category(opp.title)
    title = opp.title.lower()
    if community not in {"r/codex", "r/claudecode"}:
        return False
    if not is_high_fit(opp):
        return False
    # All workflow-fit categories that warrant a product mention: RalphWorkflow
    # answers exactly the unattended/overnight/finish-state questions these raise.
    if category in {
        "trust", "codex", "handoff", "mixed_team", "breaks_first",
        "overnight", "remote", "workflow", "approval", "announcement",
    }:
        return True
    return False


def github_link_snippets(opp: Opportunity) -> list[str]:
    """Build RalphWorkflow product CTA snippets pointing at Codeberg primary."""
    title = opp.title.lower()
    category = detect_category(opp.title)
    base = [
        (
            "If you want that workflow without handing the repo to a hosted platform, RalphWorkflow is the free/open-source version of it. "
            "It orchestrates the agent CLIs you already use on your own machine so the overnight result comes back reviewable instead of just sounding done.\n\n"
            f"{CODEBERG_PRIMARY_URL}"
        ),
        (
            "RalphWorkflow is the free/open-source tool I built for exactly this kind of repo-scale handoff: developers who want to use the agents they already have on their own machine, walk away from bigger tasks, and wake up to output they can actually review.\n\n"
            f"{CODEBERG_PRIMARY_URL}"
        ),
        (
            "If the interesting part here is not the model choice but the morning-after handoff, RalphWorkflow is my free/open-source take on that problem. "
            "It keeps the agents local on your machine and tries to leave a substantial diff plus proof instead of another long transcript.\n\n"
            f"{CODEBERG_PRIMARY_URL}"
        ),
        (
            "That is the gap RalphWorkflow is meant to close: free and open source, runs with the agent CLIs already on your own machine, and is for work too big to babysit but still serious enough that you want a reviewable result in the morning.\n\n"
            f"{CODEBERG_PRIMARY_URL}"
        ),
    ]
    if category == "trust":
        base.append(
            "If you want a concrete version of \"trust the workflow, not the confidence score,\" RalphWorkflow is a free/open-source way to do that with the agents already on your own machine and a morning-after review path instead of blind faith.\n\n"
            f"{CODEBERG_PRIMARY_URL}"
        )
    if category == "handoff":
        base.append(
            "If the problem you are solving is the Claude → Codex → review handoff, RalphWorkflow is my free/open-source take on that flow. It orchestrates the agent CLIs already on your own machine so bigger tasks can run unattended overnight and still come back as something you can review quickly in the morning.\n\n"
            f"{CODEBERG_PRIMARY_URL}"
        )
    if category == "mixed_team":
        base.append(
            "If the pain is mixed-agent team state rather than raw model quality, RalphWorkflow is the free/open-source version of a stricter handoff contract: keep the agents you already use on your own machine, let them work repo-scale jobs overnight, and wake up to a bounded result plus proof instead of session confusion.\n\n"
            f"{CODEBERG_PRIMARY_URL}"
        )
    if category in {"workflow", "codex", "breaks_first"} or "claude" in title:
        base.append(
            "If you are already thinking in builder/reviewer phases, RalphWorkflow is the free/open-source version of that flow: orchestrate the agents you already use on your own machine, let them work unattended overnight, then come back to something substantial you can inspect like a real code review.\n\n"
            f"{CODEBERG_PRIMARY_URL}"
        )
    return base


def inject_paragraph(body: str, snippet: str, position: str) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    if position == "after_first" and len(paragraphs) >= 1:
        paragraphs.insert(1, snippet)
    elif position == "before_last" and len(paragraphs) >= 2:
        paragraphs.insert(len(paragraphs) - 1, snippet)
    else:
        paragraphs.append(snippet)
    return "\n\n".join(paragraphs)


def site_language_hits(text: str) -> int:
    lowered = text.lower()
    return sum(1 for term in SITE_LANGUAGE_TERMS if term in lowered)


def stale_reddit_framing_hits(text: str) -> int:
    lowered = text.lower()
    return sum(1 for term in STALE_REDDIT_FRAMING_TERMS if term in lowered)


def product_mention_is_last_slot(body: str) -> bool:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        return False
    mention_markers = ("ralphworkflow", "ralph workflow", CODEBERG_PRIMARY_URL.lower())
    mention_indexes = [
        idx for idx, paragraph in enumerate(paragraphs)
        if any(marker in paragraph.lower() for marker in mention_markers)
    ]
    if not mention_indexes:
        return False
    return mention_indexes[-1] == len(paragraphs) - 1


def candidate_policy_issues(body: str, opp: Opportunity) -> list[str]:
    issues: list[str] = []
    site_hits = site_language_hits(body)
    if site_hits == 0:
        issues.append("missing_site_language")
    if should_add_github_link(opp) and site_hits < 2:
        issues.append("weak_site_language")
    if stale_reddit_framing_hits(body) > site_hits:
        issues.append("stale_reddit_framing_dominates")
    if should_add_github_link(opp) and product_mention_is_last_slot(body):
        issues.append("product_mention_in_last_slot")
    return issues


def one_paragraph_candidates(opp: Opportunity) -> list[str]:
    angle = " ".join((opp.angle or "").split())
    category = detect_category(opp.title)
    if not should_add_github_link(opp):
        if mention_fit_score(opp.mention_fit) == 1 and (opp.direct_reply_fit or "").strip().strip("*").lower() in {"high", "medium-high"}:
            return [
                f"What stands out to me here is {angle or 'the finish-state problem'}; the useful bar is still simple: no babysitting, finished code, tested code, ready to review, and a clear answer to what changed. If you want one concrete proof surface for that finish line instead of a pitch, this is the doc I keep pointing people to: {CODEBERG_REVIEW_PROOF_URL}"
            ]
        if angle:
            return [
                f"What stands out to me here is {angle}; the useful bar is still simple: no babysitting, finished code, tested code, ready to review, and a clear answer to what changed."
            ]
        return []
    category_bodies: dict[str, list[str]] = {
        "trust": [
            f"Trust gets a lot easier when the workflow runs on your own machine and comes back with finished code, tested code, and a plain answer to what changed. RalphWorkflow is the free/open-source operating system I built around that finish line, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
        "codex": [
            f"What matters to me in Codex threads is not blind trust but whether the run comes back on your own machine with finished code, tested code, and a reviewable answer to what changed. RalphWorkflow is the free/open-source operating system I built around that bar, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
        "approval": [
            f"Approval loops only feel useful when the run comes back on your own machine with finished code, tested code, and one short named list of what still needs a human call. RalphWorkflow is the free/open-source operating system I built around that bar, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
        "announcement": [
            f"A run-until-done mode only earns trust if it comes back on your own machine with finished code, tested code, and enough evidence to decide whether you'd actually merge it. RalphWorkflow is the free/open-source operating system I built around that finish standard, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
        "handoff": [
            f"Claude → Codex handoffs stop being impressive the moment the run returns as cleanup work; the useful version is finished code, tested code, and a small handoff you can review on your own machine. RalphWorkflow is the free/open-source operating system I built for that finish state, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
        "mixed_team": [
            f"Mixed-agent teams only help when they reduce review debt instead of multiplying it; I want finished code, tested code, and a bounded result I can judge quickly on my own machine. RalphWorkflow is the free/open-source operating system I built around that bar, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
        "workflow": [
            f"The real workflow problem is review tax, not tool count: one real task, no babysitting, finished code, tested code, and a clear answer to what changed on your own machine. RalphWorkflow is the free/open-source operating system I built around that bar, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
        "breaks_first": [
            f"What breaks first in bigger agent runs is usually the finish state, not the raw generation, so I want finished code, tested code, and a clean explanation of what changed on your own machine. RalphWorkflow is the free/open-source operating system I built around that bar, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
        "overnight": [
            f"Overnight runs only buy time when the morning result lands on your own machine as finished code, tested code, and a reviewable answer to what changed. RalphWorkflow is the free/open-source operating system I built around that morning-after standard, with Codeberg as the primary repo: {CODEBERG_PRIMARY_URL}",
        ],
    }
    if category in category_bodies:
        return category_bodies[category]
    if angle:
        return [
            f"What stands out to me here is {angle}; the useful bar is still simple: no babysitting, finished code, tested code, ready to review, and a clear answer to what changed."
        ]
    return []


def sentence_overlap_penalty(body: str, recent: list[str]) -> float:
    sentences = {s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()}
    if not sentences:
        return 0.0
    best = 0.0
    for prev in recent:
        prev_sentences = {s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", prev) if s.strip()}
        if not prev_sentences:
            continue
        overlap = len(sentences & prev_sentences) / max(len(sentences), len(prev_sentences))
        best = max(best, overlap)
    return best


def body_penalty(body: str, recent: list[str]) -> tuple[float, int]:
    penalty = 0.0
    if opening_is_repetitive(body, recent):
        penalty += 1.0
    if opening_family_repeats(body, recent):
        penalty += 0.85
    if structure_similarity(body, recent):
        penalty += 0.5
    if closing_slot_repeats(body, recent):
        penalty += 0.5
    if github_cta_repeats(body, recent):
        penalty += 1.0
    penalty += body_similarity(body, recent)
    penalty += sentence_overlap_penalty(body, recent)
    return penalty, -len(body)


def build_github_link_candidates(body: str, opp: Opportunity) -> list[str]:
    candidates = []
    for snippet in github_link_snippets(opp):
        candidates.append(inject_paragraph(body, snippet, position="after_first"))
        candidates.append(inject_paragraph(body, snippet, position="before_last"))
        if len([p for p in body.split("\n\n") if p.strip()]) <= 1:
            candidates.append(inject_paragraph(body, snippet, position="append"))
    return candidates


# 2026-05-22: structural body cadences — generated by reddit_structural_bodies.py
# These break the old 4-paragraph cadence (contrast→handoff→proof→close) with
# genuinely different paragraph structures. Each is a complete body template.
STRUCTURAL_CADENCE_BODIES: list[str] = [
    # direct_statement: lead with direct claim, support with example, close with principle
    ("The most useful constraint I found for longer AI coding runs is: the output has to be something I would actually merge.\n\n"
     "Not just 'the agent said it was done.' Not a confident summary. An actual diff I can read in five minutes, check evidence that ran, and a short named list of what still needs a call.\n\n"
     "The difference between that and most unattended runs is mostly whether you defined the finish line before starting. The few times this actually worked for me on real backlog work, it was because I wrote a one-paragraph spec first and the agent's output had to match it.\n\n"
     "Ralph Workflow: free, open-source, runs existing AI coding tools on your own machine and tries to end with finished code, tested code, and a review surface.\n\n"
     "https://codeberg.org/RalphWorkflow/Ralph-Workflow / https://github.com/Ralph-Workflow/Ralph-Workflow"),
    # question_opening: open with a question, answer with experience
    ("What's the actual test for whether an AI coding run was worth it?\n\n"
     "For me it became: did the morning-after review take less time than doing it myself would have? Not 'was the agent confident' — did the output survive contact with my actual codebase.\n\n"
     "That question started mattering more when I moved from one-off prompts to overnight runs on real backlog work. The agent is never the product. The merged result is.\n\n"
     "What changed my setup was separating the spec from the execution. Write what 'done' looks like before starting. Let the agent work. Judge the result against the spec in the morning, not the agent's self-assessment.\n\n"
     "Ralph Workflow: free and open-source, runs existing AI coding tools toward a spec'd finish line on your own machine.\n\n"
     "https://codeberg.org/RalphWorkflow/Ralph-Workflow / https://github.com/Ralph-Workflow/Ralph-Workflow"),
    # before_after: narrative — old way failed, new approach, concrete result
    ("I used to spend the first twenty minutes of every morning reconstructing what an overnight AI coding run had actually done.\n\n"
     "Which files changed. Which checks ran. What still needed a call. The agent was confident. The result was a mess.\n\n"
     "The thing that fixed it wasn't a better model or a longer prompt. It was moving the finish line definition to the start of the run instead of the end.\n\n"
     "Pick one real backlog task. Write a one-paragraph spec. Run the agent against that spec. Judge the output against it in the morning: would I merge this?\n\n"
     "Ralph Workflow: free, open-source, runs existing AI coding tools through that pattern on your own machine.\n\n"
     "https://codeberg.org/RalphWorkflow/Ralph-Workflow / https://github.com/Ralph-Workflow/Ralph-Workflow"),
    # approach_contrast: two approaches to the same problem
    ("There are two ways to run AI coding agents on real repo work.\n\n"
     "The first looks like: give the agent a task, wait for confidence, spend the morning reconstructing what actually happened. The diff is unclear, the checks are unverified, the open calls are unnamed.\n\n"
     "The second starts differently: define the finish line before running. A bounded spec. A named finish criterion. Then after the run: open the diff, read the checks, and make only the calls that actually need a human.\n\n"
     "The first approach produces confident summaries. The second produces mergeable output.\n\n"
     "Ralph Workflow: free and open-source, runs existing AI coding tools on your own machine with a spec-first workflow toward a real finish line.\n\n"
     "https://codeberg.org/RalphWorkflow/Ralph-Workflow / https://github.com/Ralph-Workflow/Ralph-Workflow"),
    # tool_example: shows a specific task type and how workflow handles it
    ("The task type where this becomes most obvious: a bounded refactor across three files with tests.\n\n"
     "You already know the spec. The agent should produce: changed files, test output, and a named list of what it couldn't finish. In the morning you open the diff, run the tests, and make three calls instead of spending an hour reconstructing what happened.\n\n"
     "What most AI coding tools skip is the 'named list of what still needs a call.' Without that, you're auditing intent instead of reviewing output.\n\n"
     "Ralph Workflow: free and open-source, tries to end every run with finished code, check evidence, and a short open-decisions list instead of a confident blob.\n\n"
     "https://codeberg.org/RalphWorkflow/Ralph-Workflow / https://github.com/Ralph-Workflow/Ralph-Workflow"),
    # opinion_statement: contrarian opening, supported with reasoning
    ("Most AI coding agents are better at seeming done than at actually being done.\n\n"
     "The confidence is not the product. The diff you can read in five minutes, the check evidence that actually ran, the named list of what still needs a call — that's the product.\n\n"
     "The shift that made overnight runs worth it for me: stop treating 'the agent said it was done' as a finish signal. Treat 'would I merge this' as the finish signal.\n\n"
     "Ralph Workflow: free and open-source, tries to enforce exactly that — run existing AI coding tools against a real spec, end with mergeable output and a short open-decisions list.\n\n"
     "https://codeberg.org/RalphWorkflow/Ralph-Workflow / https://github.com/Ralph-Workflow/Ralph-Workflow"),
]


def comment_candidates(opp: Opportunity, retro: dict | None = None) -> list[str]:
    variants = build_comment_variants(opp, retro=retro)
    candidates: list[str] = []
    high_fit_codeberg = should_add_github_link(opp)
    candidates.extend(one_paragraph_candidates(opp))
    for variant in variants:
        if high_fit_codeberg:
            candidates.extend(build_github_link_candidates(variant, opp))
        else:
            candidates.append(variant)
    # 2026-05-22: structural cadences are useful for high-fit repo-conversion threads,
    # but they should not force a product CTA into generic/medium-fit threads.
    if high_fit_codeberg:
        candidates.extend(STRUCTURAL_CADENCE_BODIES)
    return candidates


# 2026-05-20: repair -- repetitive_outreach.
# Subreddit-specific templates exist but were unreachable because the lookup
# used detect_category(title) which never produces community-keyed strings.
# Fix: community-first lookup before falling back to category-based templates.
_SUBREDDIT_ARCHETYPES: dict[str, list[str]] = {
    "r/ClaudeCode": [
        # Pain: approval/babysitting — rescue-loop opening
        (
            "The Claude Code pain point that burns me out fastest is the rescue loop: approve, wait, hit another pause, then realize I still do not know whether the repo is in a safer state.\n\n"
            "What actually helps is forcing the run to end with a bounded diff, check output, and a short list of decisions it could not make alone. Then approval becomes a judgment step instead of a babysitting habit.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: approval/babysitting — interrupted-focus opening
        (
            "Getting yanked back into Claude Code every twenty minutes is not just annoying — it kills the whole point of using it for deeper work.\n\n"
            "The only version I trust is one where the pause comes with repo evidence: what changed, what checks ran, and what still needs a human call. Without that, approval is just interruption with branding.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: approval/babysitting — morning-regret opening
        (
            "The worst Claude Code approvals are the ones that feel harmless at night and create cleanup work the next morning.\n\n"
            "That usually means the workflow never demanded a clear finish state. If the run cannot hand back a readable diff, real verification, and a tiny unresolved list, it has not earned the next approval yet.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # 2026-05-20 repair: add 3 fresh openings for r/ClaudeCode to break stale cadence
        # Pain: approval/babysitting — clean-stop opening
        (
            "The question I now ask before any Claude Code approval: does the run end with a diff I can read in two minutes, checks that actually ran, and a named short list of what still needs judgment?\n\n"
            "If it ends with a confident paragraph instead, the approval is not a judgment call — it is a gamble.\n\n"
            "Ralph Workflow is free and open-source: it enforces that bounded finish so the next approval is grounded."
        ),
        # Pain: approval/plan-mode — framing opening
        (
            "Plan mode and approval loops are only worth it if the finish state is defined before the run starts.\n\n"
            "Without a bounded diff, check evidence, and a short receipt of open decisions, plan mode mostly just gives the agent more rope to hang itself with.\n\n"
            "Ralph Workflow is free and open-source: it enforces that finish framing so plan mode actually means something."
        ),
        # Pain: approval — cost-benefit opening
        (
            "The approval fatigue is real: every interrupt is a context switch that costs more than the time it saves.\n\n"
            "The fix is not fewer approvals — it is a tighter finish contract so each approval is a real gate, not a reflex.\n\n"
            "Ralph Workflow is free and open-source: it enforces that contract so the approval surface stays small."
        ),
    ],
    "r/AI_Agents": [
        # Pain: multi-agent chaos — baton-drop opening
        (
            "The ugly part of multi-agent work is not getting the first output — it is discovering nobody owns the baton once one agent hands off to the next.\n\n"
            "That is where vague context, duplicated work, and silent contradictions pile up. A useful chain needs each pass to leave a scoped diff, check evidence, and a clear unresolved handoff instead of another blob of intent.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: multi-agent chaos — state-collision opening
        (
            "What makes multi-agent repo work feel fragile is state collision: two agents both sound reasonable, then the combined result quietly stops making sense.\n\n"
            "The only pattern I have found reliable is making one pass responsible for the shared state check after the others finish, with the changed files, validation output, and open risks written down.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: multi-agent chaos — reconstruction-cost opening
        (
            "If the human has to reconstruct what three agents meant after they all touched the same repo, the system did not scale — it externalized the cost.\n\n"
            "Good multi-agent flow means every step shrinks ambiguity for the next one: bounded task, visible diff, checks attached, unresolved calls named. Otherwise the last reviewer inherits all the chaos.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/ClaudeAI": [
        # Pain: autonomy/trust — definition-first opening
        (
            "The real question for autonomous agent runs is not whether it can run unattended — it is what it should hand back when it stops.\n\n"
            "If the answer is finished code, passed checks, and a short list of what still needs judgment, unattended is fine. If it ends with a confident summary and no proof, autonomy just means you inherited a bigger review problem.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: autonomy/trust — failure-first opening
        (
            "The autonomy failure I run into most is not the agent doing something obviously wrong. It is the agent confidently doing the wrong thing and stopping as if it were finished.\n\n"
            "Explicit done criteria, automated checks, and a short receipt of unresolved calls is what makes unattended runs trustworthy instead of just longer.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: autonomy/trust — contrast-first opening
        (
            "Finished code, passed checks, and a short unresolved list — that is what trustworthy unattended output looks like. Anything less is just a longer prompt in disguise.\n\n"
            "The autonomy gap is not runtime. It is the absence of a bounded finish state. Ralph Workflow tries to close that gap so unattended actually means something you can trust.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/Python": [
        # Pain: scripting/automation — maintenance-cost opening
        (
            "A lot of Python automation feels productive right up until you become the unpaid maintainer of every weird edge case it created overnight.\n\n"
            "What keeps that manageable is making the run leave behind evidence instead of vibes: changed files, check results, and a short note on what still needs human judgment. Otherwise automation just shifts the debugging later.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: scripting/automation — hidden-review-debt opening
        (
            "The hidden cost in a lot of Python workflow automation is review debt — the code ran, but now somebody has to reverse-engineer whether the result is safe to keep.\n\n"
            "I have had better luck treating the finish as part of the automation itself: bounded change, verification attached, unresolved edge cases written down. That makes the next morning shorter instead of stranger.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: scripting/automation — script-vs-system opening
        (
            "The difference between a handy Python script and an automation system that actually saves time is whether you can trust what it hands back without rereading the whole story.\n\n"
            'For bigger coding tasks, I want the output to be inspectable in repo terms: a readable diff, real checks, and explicit leftovers. That is the part most "automation" skips.\n\n'
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/devops": [
        # Pain: scripting/automation — same as r/Python, different archetype label
        (
            "The gap between 'I automated this' and 'this actually saved me time' is usually the review step — if the output is not bounded and checkable, automation just moves the work around.\n\n"
            "The fix: spec before, receipt after. What changed, what passed, and what still needs judgment. That turns a script into something you can actually trust overnight.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        (
            "If you are running Claude Code or Codex on real engineering tasks and the session ends with a confident paragraph instead of a diff you can actually inspect, the workflow needs a tighter finish contract.\n\n"
            "One bounded diff, one check bundle, one short receipt of unresolved decisions. That is the finish standard that makes unattended runs reviewable instead of just long.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        (
            "The automation pattern that actually holds up: spec before, receipt after. What changed, what passed, what still needs judgment. Everything else is just a longer script.\n\n"
            "Small scoped task, automated checks, and a clean diff at the end — that is the finish state that makes unattended automation worth running overnight.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/LocalLLaMA": [
        # Pain: engineering workflow — outcome-first opening
        (
            "What separates an overnight run that was worth it from one that just made the morning more complicated is usually the spec written before it started, not the model inside it.\n\n"
            "One bounded diff, one check bundle, one short receipt of unresolved decisions. That is the finish standard that makes unattended runs reviewable instead of just long.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: engineering workflow — hypothesis-first opening
        (
            "Hypothesis: most AI coding workflow failures are not capability failures — they are handoff failures, where nobody defined what the next phase should receive.\n\n"
            "The fix: explicit handoff receipts between phases. What changed, what ran, what still needs judgment. That turns multi-phase runs into bounded checks instead of reconstruction projects.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Pain: engineering workflow — principle-first opening
        (
            "One bounded diff, one check bundle, one short receipt of unresolved decisions. That is the finish standard that makes unattended runs reviewable instead of just long.\n\n"
            "The automation pattern that actually holds up: spec before, receipt after. Everything else is just a longer script.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    # 2026-05-20 repair: repetitive_outreach — missing archetypes seeded from reddit_fresh_openings.md F-P
    "r/codex": [
        # Opening H — finish-line contrast, soft
        (
            "Most tooling talk focuses on the start — which model, which context window, which parallel branch. The part that actually determines whether you close the laptop is the finish: what changed, what ran, what still looks off. That is where the real workflow problem lives.\n\n"
            "Ralph Workflow is free and open-source: it enforces a reviewable finish state instead of a longer transcript.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Opening K — repo-state anxiety
        (
            "The failure mode I care about is not whether the agent looked productive. It is whether I can open the repo later and understand exactly what changed, what passed, and what still needs a human call.\n\n"
            "Ralph Workflow is free and open-source: it enforces explicit handoff receipts so the repo state is always reconstructable.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Opening F — visceral failure story
        (
            "The pattern I see most is: you write a task, the agent starts, you answer a prompt, then another, it hallucinates, you correct it, and you are still there at midnight babysitting a tool that was supposed to save you time. The fix is not a better prompt — it is a clearer finish line.\n\n"
            "Ralph Workflow is free and open-source: it enforces a bounded diff, check bundle, and unresolved list at every phase end.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/entrepreneur": [
        # Opening I — bounded-cost / fail-closed
        (
            "The overnight run I regret most was not the one that failed — it was the one that seemed to succeed. No visible diff, no clear receipt, just a quiet feeling that something had happened. Bounded cost with a reviewable result would have caught it.\n\n"
            "Ralph Workflow is free and open-source: it enforces a bounded finish contract so you know exactly what you are waking up to.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Opening M — bounded overnight wager
        (
            "The only overnight agent runs worth repeating are the ones with a bounded downside by morning. If I cannot tell what changed and what still looks risky in five minutes, the run was too open-ended.\n\n"
            "Ralph Workflow is free and open-source: it enforces a bounded finish receipt so unattended runs stay worth running.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/startups": [
        # Opening I — bounded-cost / fail-closed
        (
            "The overnight run I regret most was not the one that failed — it was the one that seemed to succeed. No visible diff, no clear receipt, just a quiet feeling that something had happened. Bounded cost with a reviewable result would have caught it.\n\n"
            "Ralph Workflow is free and open-source: it enforces a bounded finish contract so you know exactly what you are waking up to.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Opening M — bounded overnight wager
        (
            "The only overnight agent runs worth repeating are the ones with a bounded downside by morning. If I cannot tell what changed and what still looks risky in five minutes, the run was too open-ended.\n\n"
            "Ralph Workflow is free and open-source: it keeps your agents on your own machine and enforces a reviewable finish state.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/programming": [
        # Opening G — what did you actually ship?
        (
            "The overnight run question is usually not 'which agent should run longer' — it is 'what will I actually be able to review in the morning.' Most setups answer the first question and completely skip the second.\n\n"
            "Ralph Workflow is free and open-source: it enforces a bounded diff, check bundle, and unresolved receipt so the morning review is short instead of a reconstruction project.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Opening L — anti-transcript angle
        (
            "A lot of agent workflow advice still assumes the transcript is the artifact. For real repo work, the artifact has to be the diff plus the proof bundle — otherwise you are doing transcript archaeology instead of review.\n\n"
            "Ralph Workflow is free and open-source: it enforces diff-plus-proof as the finish artifact, not the transcript.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/experienceddevs": [
        # Opening K — repo-state anxiety
        (
            "The failure mode I care about is not whether the agent looked productive. It is whether I can open the repo later and understand exactly what changed, what passed, and what still needs a human call.\n\n"
            "Ralph Workflow is free and open-source: it enforces explicit handoff receipts so the repo state is always reconstructable after every phase.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Opening L — anti-transcript angle
        (
            "A lot of agent workflow advice still assumes the transcript is the artifact. For real repo work, the artifact has to be the diff plus the proof bundle — otherwise you are doing transcript archaeology instead of review.\n\n"
            "Ralph Workflow is free and open-source: it enforces diff-plus-proof as the finish artifact so the next reviewer inherits clarity, not confusion.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
    "r/SideProject": [
        # Opening M — bounded overnight wager
        (
            "The only overnight agent runs worth repeating are the ones with a bounded downside by morning. If I cannot tell what changed and what still looks risky in five minutes, the run was too open-ended.\n\n"
            "Ralph Workflow is free and open-source: it keeps the agents on your own machine and enforces a reviewable finish receipt so side project momentum does not get killed by a bad morning-after state.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
        # Opening I — bounded-cost / fail-closed
        (
            "The overnight run I regret most was not the one that failed — it was the one that seemed to succeed. No visible diff, no clear receipt, just a quiet feeling that something had happened. Bounded cost with a reviewable result would have caught it.\n\n"
            "Ralph Workflow is free and open-source: it enforces a bounded finish contract so side project runs do not create cleanup debt.\n\n"
            "Primary repo (Codeberg): https://codeberg.org/RalphWorkflow/Ralph-Workflow"
        ),
    ],
}


def _subreddit_archetype(community: str) -> str | None:
    """Map a normalized community name to its archetype key in _SUBREDDIT_ARCHETYPES."""
    normalized = normalize_community(community).lower()
    for archetype_key in _SUBREDDIT_ARCHETYPES:
        if archetype_key.lower().replace("/", "") in normalized or normalized.replace("/", "") in archetype_key.lower().replace("/", ""):
            return archetype_key
    return None


def build_comment_variants(opp: Opportunity, retro: dict | None = None) -> list[str]:
    retro = retro or {}
    recommendations = " ".join(retro.get("recommendations") or [])
    emphasize_review = "reviewability" in recommendations.lower() or "reviewable" in recommendations.lower()
    category = detect_category(opp.title)

    # 2026-05-21: repair -- community archetypes were swallowing title-specific categories,
    # which pushed /goal and approval-loop threads through the same generic r/ClaudeCode body.
    # Prefer category-specific variants whenever we can classify the pain; keep community
    # archetypes as the fallback when the title is too generic to classify cleanly.
    archetype = _subreddit_archetype(opp.community)
    if category == "generic" and archetype and archetype in _SUBREDDIT_ARCHETYPES:
        return _SUBREDDIT_ARCHETYPES[archetype]

    variants: dict[str, list[str]] = {
        "trust": [
            (
                "The overnight run problem is usually not the agent — it is the lack of a clear stop condition. Without a bounded definition of done, you wake up to either nothing or something you cannot trust.\n\n"
                "The fix is not a better model, it is a tighter handoff contract: scope before it runs, diff plus checks at the end, and a short receipt for anything still uncertain.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates your existing AI coding agents and tries to leave you a result you can actually evaluate."
            ),
            (
                "What I kept getting wrong early on was treating 'the agent said it was done' as the same thing as 'the job is actually done.' The distinction matters most when you come back to the result the next morning.\n\n"
                "Small scoped task, isolated workspace, explicit done criteria, and a final receipt beats a long transcript almost every time.\n\n"
                "Ralph Workflow is free and open-source: it runs the AI coding tools you already use and tries to make the output something you can actually evaluate."
            ),
            (
                "The phase that usually gets skipped is the review pass — not because it is unnecessary, but because nobody defined what it should produce.\n\n"
                "A bounded diff, proof that checks ran, and explicit open questions turn the review step into a bounded check rather than a blank slate.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates your AI coding agents and enforces a reviewable handoff at the end of each phase."
            ),
        ],
        "codex": [
            # 2026-05-19: refreshed — old bodies opened with advice-patterns that were repeated
            # New pattern: specific failure scenario first
            (
                "The failure mode I kept hitting with Claude + Codex wasn't model quality — it was deciding which output to actually trust.\n\n"
                "The setup that fixed it: one tool builds against a written spec, the second reviews against that same spec. The run ends when the diff is quick to read and the checks are attached, not when one agent says it is done.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates the handoff between tools and enforces a reviewable finish state."
            ),
            # New pattern: outcome-first with concrete contrast
            (
                "What I wanted from a Claude + Codex setup was not two opinions — it was a builder and an auditor with a shared spec.\n\n"
                "The second pass does not need to be another implementation pass. It needs a written adversarial job: find what the first pass assumed but did not verify. That is where the real gaps hide.\n\n"
                "Ralph Workflow is free and open-source: it adds spec-driven handoff and checkpointing to your existing Claude Code or Codex workflow."
            ),
            # New pattern: constraint-first, no advice opening
            (
                "The constraint I set for my Claude + Codex runs: the second tool never re-implements. It only reviews and flags.\n\n"
                "That alone removed most of the overlap confusion. The result is a diff, check results, and a short receipt of what still needs human call — not two versions of the same change to reconcile.\n\n"
                "Ralph Workflow is free and open-source: it enforces role separation and preserves a reviewable handoff after each run phase."
            ),
        ],
        "handoff": [
            # 2026-05-19: refreshed — old bodies opened with advice/contrast patterns repeated across subreddits
            # New pattern: pain-first, specific failure mode
            (
                "The problem with multi-hop Claude workflows is not the model intelligence — it is that nobody defines what each hop owes the next.\n\n"
                "The fix I found most reliable: each handoff carries three things — the scoped task, the current diff, and what still looks off. The final human read stays bounded instead of becoming a full reconstruction.\n\n"
                "Ralph Workflow is free and open-source: it enforces structured handoffs and checkpointing between run phases."
            ),
            # New pattern: scenario-first, no advice opening
            (
                "I tried a three-pass Claude setup. The bottleneck was not agent capability — it was the handoff paperwork. Nobody had written down what the second pass should accept as input.\n\n"
                "The fix: a tight handoff contract for each hop — what changed, what ran, what still needs judgment. Then the final review is a bounded check, not a reconstruction project.\n\n"
                "Ralph Workflow is free and open-source: it enforces explicit handoff receipts between each run phase."
            ),
            # New pattern: constraint-first, concrete outcome
            (
                "What I had to enforce after too many tangled multi-hop runs: each pass produces a three-item receipt before the next one starts.\n\n"
                "That constraint alone removed most of the \'whose output do I trust\' problem. The result is a diff, check results, and a short unresolved list — not a transcript and a \'done\' claim from each agent.\n\n"
                "Ralph Workflow is free and open-source: it adds structured handoff receipts and checkpoint resume to your existing workflow."
            ),
        ],
        "mixed_team": [
            (
                "Mixed-agent teams usually break on state, not raw coding ability. One session assumes permission or context that the next session never actually inherited.\n\n"
                "What helps is a stricter contract: one owner for each shared boundary, explicit handoff notes between passes, and a final review state that says what changed, what ran, and what still needs judgment.\n\n"
                "Without that, adding Gemini or Codex mostly just adds another place for assumptions to drift."
            ),
            (
                "If you're running Claude Code with Gemini or Codex, I'd design for stable handoffs before I optimized for more throughput.\n\n"
                "The painful failures are usually permission mismatch, unverified assumptions, and nobody owning the cross-cutting bits like config, schema, or tests.\n\n"
                "One owner per shared boundary plus a short finish receipt does more for trust than another parallel branch."
            ),
            (
                "Agent teams get interesting right up until the morning-after question becomes: who actually validated the combined result?\n\n"
                "My bias is to let specialists fan out, but require a single closing pass that rebuilds the merged state, reports the checks, and calls out any assumptions the other sessions might have invalidated.\n\n"
                "That keeps the team useful without pretending the handoff solved itself."
            ),
        ],
        "breaks_first": [
            (
                "What breaks first for me is rarely the branch isolation. It's confidence in the merged state after a few separate agent runs all touched related assumptions.\n\n"
                "The fast fix is not more worktrees. It's one owner for shared boundaries and a finish receipt that says what changed, what ran, and what still needs a human decision before merge.\n\n"
                "That turns the morning-after review into a bounded check instead of a reconstruction project."
            ),
            (
                "The first thing I stop trusting in multi-agent runs is the space between the branches. Config, schema, tests, and other shared boundaries drift quietly even when every individual branch looks clean.\n\n"
                "So I want the end state to prove the merged repo still holds up, not just that each agent completed its local task.\n\n"
                "If that proof is missing, the clean merge is mostly cosmetic."
            ),
            (
                "For me the sharpest failure mode is review overhead. The agents may finish their pieces, but the human has to reconstruct the cross-branch story from scratch.\n\n"
                "A short finish receipt plus merged-state checks helps more than another layer of parallelism because it gives you a clean re-entry point the next morning.\n\n"
                "That is usually where trust is won or lost."
            ),
        ],
        "approval": [
            # 2026-05-20: fresh openings for approval category — different pain angles, no reused cadence
            # Pain angle: midnight babysitting gets old fast
            (
                "Approval mode works until 2am when you're still clicking approve on runs that handed you a prompt instead of finished code, tested code.\n\n"
                "The fix is not fewer prompts. It is a finish contract that owes you finished code, test results, and a short explicit list of what still needs your call. Without that, approval is just queue management.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates your AI coding agents and enforces that finish contract on your own machine."
            ),
            # Pain angle: approval without evidence is just trust fall
            (
                "The approval loop I stopped running was the one where the agent paused, I clicked approve, and then spent the next morning untangling what actually changed.\n\n"
                "What replaced it: a finish standard that requires finished code, tested code, and a short unresolved list before approval. That turns the gate into a real check instead of a ritual.\n\n"
                "Ralph Workflow is free and open-source: it enforces that standard so approval becomes a meaningful judgment instead of a reflex."
            ),
            # Pain angle: waiting for approval that should have been automatic
            (
                "If the run had a clear finish standard — bounded diff, tests that passed, open questions named — approval would be fast. The pain is when the agent pauses without that.\n\n"
                "The workflow that fixed this for me: spec before, receipt after. What changed, what passed, and what still needs your call. That makes approval a judgment call instead of a guess.\n\n"
                "Ralph Workflow is free and open-source: it runs that loop so you approve results, not promises."
            ),
            # Original variants preserved (penalty system will filter if repetitive)
            (
                "The approval loop that eats your night is usually the one where the tool still has no crisp place to stop.\n\n"
                "If every pause just asks you to rescue the run, the workflow is using you as the fallback control plane instead of handing back something finished enough to judge.\n\n"
                "What helps is a tighter finish contract: bounded task, checks attached, and explicit unresolved calls when the run stops."
            ),
            (
                "Double-confirmation pain is usually a symptom, not the disease.\n\n"
                "The real miss is that the run never lands in a shape you can grade quickly: what changed, what passed, and what still needs your call.\n\n"
                "Once that finish surface is clear, approval becomes a deliberate gate instead of midnight babysitting."
            ),
        ],
        "announcement": [
            # 2026-05-20: fresh openings for announcement/run-until-done category — different pain angles
            # Pain angle: longer runtime without better landing is just more of the same problem
            (
                "The problem with \"run until done\" is not the running. It is the \"done\" — most agents mean 'I stopped' not 'the result is actually ready to merge.'\n\n"
                "What makes the feature actually useful: finished code, tested code, and a short note on what still looks risky. Without that, longer runtime just means more output to reconstruct.\n\n"
                "Ralph Workflow is free and open-source: it enforces that finish standard on your own machine so \"run until done\" actually ends in something you can verify."
            ),
            # Pain angle: when done means confident summary instead of actual proof
            (
                "The feature I want from a longer-running agent mode is not more output. It is a finish I can actually verify in the morning.\n\n"
                "If it ends with a confident paragraph, I still have to reconstruct what changed. If it ends with finished code, tested code, and open questions named — that is when unattended actually works.\n\n"
                "Ralph Workflow is free and open-source: it runs that finish-first standard on your existing agents."
            ),
            # Pain angle: runtime is easy, trustworthy stop condition is the hard part
            (
                "Making an agent run longer is a config change. Making it stop with something you can actually review is a workflow design problem.\n\n"
                "The \"run until done\" feature is interesting only if the done is defined before the run starts: scoped change, diff, checks, and unresolved items. Without that, longer runtime just pushes the review problem downstream.\n\n"
                "Ralph Workflow is free and open-source: it enforces that stop-condition discipline so the overnight run actually lands in something mergeable."
            ),
            # Original variants preserved (penalty system will filter if repetitive)
            (
                "Most autonomy announcements focus on the start button. I care more about the landing.\n\n"
                "If \"run until done\" ends with finished code, tested code, and a short note on what still looks risky, great. If it ends with a confident summary, you still inherited the same review problem."
            ),
        ],
        "remote": [
            (
                "Remote supervision usually becomes a crutch when the local workflow still assumes a human will keep rescuing it.\n\n"
                "One isolated task, explicit stop conditions, and a finish note with the diff + checks gets you much further than better remote controls.\n\n"
                "Then when you check back in, you're reviewing an outcome instead of steering a live session."
            ),
            (
                "The setups that feel good remotely are usually the ones that already had good stop conditions locally.\n\n"
                "If the run knows when to pause, what to report, and how to leave a reviewable handoff, you can walk away without feeling like you're abandoning it.\n\n"
                "Without that, remote access mostly just lets you supervise messy runs from farther away."
            ),
        ],
                "overnight": [
            # 2026-05-19: repair -- added 3 fresh openings for overnight category
            # Opener style: concrete metric first
            (
                "The single thing that determines whether an overnight run is worth it: do you wake up to a diff you can read in five minutes or to a pile of prompts?\n\n"
                "Everything that makes overnight runs useful comes back to that. Small scope, explicit done criteria, checks that ran, and a short unresolved list. Without those four things, the morning is just output archaeology.\n\n"
                "Ralph Workflow is free and open-source: it runs your existing AI coding agents and tries to make the morning result worth waking up to."
            ),
            # Opener style: misconception correction
            (
                "Overnight runs do not fail because the agent is not smart enough. They fail because nobody told it when to stop and what the finish line looks like.\n\n"
                "The fix is not a more capable agent -- it is a bounded task, automated verification, and a receipt for what still needs judgment. Those three things make the difference between a useful overnight run and a morning you cannot recover.\n\n"
                "Ralph Workflow is free and open-source: it adds that discipline to the AI coding tools you already use on your own machine."
            ),
            # Opener style: process-oriented
            (
                "What I run overnight is always the same shape: one substantial task, a spec written before bed, a diff and test results waiting in the morning.\n\n"
                "That structure does not guarantee a perfect result, but it guarantees a reviewable one. And a reviewable failure is always better than an unreviewable success.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates that loop so the morning result is bounded and ready to judge."
            ),
            # Original variants preserved
            (
                "Overnight drift usually starts way before the morning. It starts when the task is too fuzzy and the agent has no crisp finish line.\n\n"
                "The runs that hold up for me have small scope, explicit done criteria, hard verification steps, and a final bundle of what changed and what still needs judgment.\n\n"
                "That doesn't remove mistakes, but it does make the morning-after result reviewable instead of mysterious."
            ),
            (
                "Scope creep is what burns most overnight runs for me, not some dramatic model collapse.\n\n"
                "So I bias hard toward tasks that can end with one readable diff, one check bundle, and one note about unresolved decisions.\n\n"
                "If the handoff is clean, the overnight run was useful even when it didn't finish everything."
            ),
        ],
        "workflow": [
            (
                "I'd tighten the finish line before adding more agent cleverness.\n\n"
                "Worktrees help with collisions, but merge safety comes from the last mile: explicit acceptance criteria, one readable diff, checks attached, and a short note on anything still uncertain.\n\n"
                "If that handoff is weak, the repo can merge cleanly and still be wrong."
            ),
            (
                "What makes these workflows feel safe isn't parallelism, it's whether the result comes back in a shape you'd actually review quickly.\n\n"
                "My bias is:\n- one concrete deliverable per branch/worktree\n- done criteria written before execution\n- final pass that reports what changed, what ran, and what still needs a human decision\n\n"
                "That catches a lot of the \"clean merge, wrong outcome\" problem."
            ),
            (
                "If I were critiquing a multi-agent workflow, I'd mostly look at the handoff between \"agent says done\" and \"human is ready to merge.\"\n\n"
                "That's where a lot of quiet failures hide. Not because the branch conflicts, but because nobody forced a small, reviewable end state with checks and open questions attached.\n\n"
                "The more boring and explicit that final step is, the safer the whole workflow feels."
            ),
        ],
        "generic": [
            # 2026-05-19: repair — replaced stale openings with pain-specific openers
            # Opener style: concrete outcome first
            (
                "The metric I use for an overnight run is simple: did it leave a diff I can read in two minutes and a short list of what still needs judgment?\n\n"
                "If yes, it was useful. If not, no amount of agent sophistication closes the gap. Small scope, explicit done criteria, and a short receipt beats a confident summary every time.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates the AI coding tools you already run and tries to leave you a result you can actually evaluate."
            ),
            # Opener style: question-first pain angle
            (
                "When does an AI coding run actually become useful? For me it is not when the agent says it is done — it is when I can open the diff, run the tests, and make a call in under five minutes.\n\n"
                "That standard is harder to meet than it sounds, but it is the difference between an overnight run that was worth it and one that just made the morning more complicated.\n\n"
                "Ralph Workflow is free and open-source: it runs your existing AI coding agents and tries to hand back something bounded and reviewable."
            ),
            # Opener style: contrast / misconception correction
            (
                "Most overnight run failures are not model failures. They are finish-line failures — nobody defined what the end state should look like, so the agent called it done and left the human to figure out the rest.\n\n"
                "The fix is not a smarter agent, it is a bounded task with an explicit stop condition: scoped change, diff, checks, and unresolved questions spelled out.\n\n"
                "Ralph Workflow is free and open-source: it adds that discipline to the agents you already use on your own machine."
            ),
            # Opener style: specific pain / what actually helps
            (
                "The thing I see trip up Claude Code users most often is treating the session transcript as the output. It is not — the output is the diff and the test results.\n\n"
                "Once I started judging runs on what landed in the repo rather than what the agent said, the review step got much faster and the handoff got much cleaner.\n\n"
                "Ralph Workflow is free and open-source: it runs the AI coding tools you already use and tries to make the output something you can actually evaluate."
            ),
            # 2026-05-20: fresh replacements for banned-opening legacy variants above
            (
                "The hardest part of overnight runs is not the runtime — it is the definition of done. Most agents treat 'I stopped' as the finish line, not 'the result is reviewable.'\n\n"
                "What has worked for me: a spec written before execution, a bounded diff at the end, and a short receipt for anything the agent flagged as uncertain. That makes the morning review a quick check rather than a reconstruction project.\n\n"
                "Ralph Workflow is free and open-source: it runs the AI coding tools you already use and tries to leave you a result you can actually evaluate."
            ),
            (
                "The multi-tool setup I kept returning to: one agent builds against a written spec, a second reviews against that same spec. The stop condition is a readable diff plus proof the checks ran.\n\n"
                "The version that actually holds up: judge the result on the diff plus checks rather than on either tool's self-report.\n\n"
                "Ralph Workflow is free and open-source: it handles the orchestration loop so you can focus on reviewing the output."
            ),
        ],
        # 2026-05-19: repair — new subreddit pain-specific categories
        "subreddit Pain: r/ClaudeCode approval/babysitting": [
            # Opener style: direct pain acknowledgment + actionable pivot
            (
                "Approval mode is useful until it turns into 'click to approve, then rescue the run anyway.' The fix is not fewer clicks — it is a tighter finish contract.\n\n"
                "A run that ends with finished code, passed checks, and a short list of what still needs judgment gives you something real to approve. A run that ends with a confident summary gives you a next prompt.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates your AI coding agents and enforces that finish contract on your own machine."
            ),
            # Opener style: concrete comparison
            (
                "The difference between an approval loop that saves you time and one that just delays the inevitable: does the run end with something you can grade in two minutes, or with another prompt waiting for you?\n\n"
                "If it is the latter, no amount of clickable UI closes the gap. You need a bounded task, real checks, and a short receipt of unresolved questions.\n\n"
                "Ralph Workflow is free and open-source: it runs your existing AI coding agents and tries to leave a result you can actually approve."
            ),
        ],
        "subreddit Pain: r/AI_Agents multi-agent chaos": [
            # Opener style: pattern recognition + specific fix
            (
                "Multi-agent setups fail in a predictable place: not at generation, but at the handoff between agents. One session leaves vague edits, the next inherits confusion.\n\n"
                "The fix is a strict handoff contract: scoped task, diff, explicit open questions. That turns agent teams from chaos into a structured review chain.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates multiple agent passes with explicit handoffs so each phase leaves the next one less ambiguous."
            ),
            # Opener style: consequence-first
            (
                "When two AI agents touch the same repo without a shared finish contract, you do not get the best of both — you get the intersection of their assumptions.\n\n"
                "One bounded task, one clean diff, one set of checks, and one short receipt of unresolved decisions per pass prevents that drift from compounding.\n\n"
                "Ralph Workflow is free and open-source: it runs multiple agent passes in sequence with that handoff discipline on your own machine."
            ),
        ],
        "subreddit Pain: r/ClaudeAI autonomy/trust": [
            # Opener style: trust-but-verify framing
            (
                "The autonomy question for AI coding agents is not 'should it run unattended?' — it is 'what should it hand back when it stops?'\n\n"
                "If the answer is finished code, passed checks, and a short list of what still needs judgment, unattended is fine. If it ends with a confident summary and no proof, autonomy just means you inherited a bigger review problem.\n\n"
                "Ralph Workflow is free and open-source: it runs your AI coding agents unattended and tries to leave a result you can actually trust."
            ),
            # Opener style: specific failure mode
            (
                "The autonomy failure mode I run into most is not the agent doing something obviously wrong — it is the agent doing the wrong thing confidently and stopping as if it were done.\n\n"
                "Explicit done criteria, automated checks, and a short receipt of unresolved calls is what makes unattended runs trustworthy instead of just longer.\n\n"
                "Ralph Workflow is free and open-source: it adds that structure to the agents you already run so the result is trustworthy without requiring you to watch."
            ),
        ],
    }
    return variants.get(category, variants["generic"])


def _last_resort_safe_body(opp: Opportunity) -> str:
    category = detect_category(opp.title)
    opener_map = {
        "approval": "The useful approval standard is finished code, tested code, and a short note on what changed before anyone clicks through.",
        "announcement": "A longer-running mode only matters if it comes back with finished code, tested code, and enough evidence to decide whether you'd actually merge it.",
        "handoff": "Mixed-tool runs only get easier when they come back with finished code, tested code, and a finish state you can judge quickly.",
        "mixed_team": "Multiple agents only help when the output stays inspectable enough for a human to judge quickly and still comes back as finished code, tested code, and a bounded result.",
        "breaks_first": "The first thing I want back from an agent run is visible proof of what changed, what passed, and whether the result is ready to review.",
        "codex": "Model choice matters less than whether the workflow comes back with finished code, tested code, and a clean answer to what changed.",
        "workflow": "A workflow is only trustworthy when it returns finished code, tested code, and a clean review surface.",
    }
    opener = opener_map.get(category, "The useful bar here is finished code, tested code, and a clean answer to what changed before the run counts as done.")
    product_sentence = f"Ralph Workflow is a free/open-source example of that approach on your own machine, with the primary repo on Codeberg: {CODEBERG_PRIMARY_URL}"
    closer = "If the run cannot do that without babysitting, it is still pushing the hard part back onto the human."
    return f"{opener} {product_sentence}\n\n{closer}"


def emergency_rewrite(opp: Opportunity, recent: list[str] | None = None) -> str:
    recent = recent or []
    category = detect_category(opp.title)
    # 2026-05-20: complete rewrite — every fallback body avoids banned cadences and
    # banned opening prefixes. Each body opens with a distinct rhetorical shape.
    if category == "approval":
        fallback_bodies = [
            # Structure: visceral pain first → finish standard
            (
                "Approval mode stops feeling useful when every pause means another rescue loop instead of a real judgment call.\n\n"
                "The version that actually works: the run ends with finished code, passed checks, and a short named list of what still needs a human call. That turns approval into a real gate instead of a reflex.\n\n"
                "Ralph Workflow is free and open-source: it orchestrates your AI coding agents so the finish comes back reviewable."
            ),
            # Structure: metaphor opener → concrete stop condition
            (
                "The session transcript is not the output — the diff plus the test results are the output.\n\n"
                "Once I started treating every pause as a handoff that needed a clean receipt, approval stopped feeling like babysitting.\n\n"
                "Ralph Workflow is free and open-source: it enforces that receipt so you approve evidence, not promises."
            ),
            # Structure: constraint-first → bounded outcome
            (
                "What I set as the approval gate: finished code, a diff I can read in two minutes, and no unresolved decisions longer than a sentence.\n\n"
                "If the agent stops short of that, no amount of clickable UI closes the gap.\n\n"
                "Ralph Workflow is free and open-source: it runs that finish standard so the approval decision is grounded."
            ),
        ]
    elif category == "announcement":
        fallback_bodies = [
            # Structure: consequence-first → concrete evaluation criterion
            (
                "A longer-running mode is only worth it if the landing is reviewable in the morning.\n\n"
                "Finished code, passed checks, and a named list of open decisions beats a confident \"done\" every time. Without that, longer runtime just buys more ambiguity.\n\n"
                "Ralph Workflow is free and open-source: it enforces that finish standard on your own machine."
            ),
            # Structure: question-first → handoff standard
            (
                "What I would check first in any \"run until done\" release: does the job end with a diff, a test run, and a short list of what still needs a call?\n\n"
                "If not, the feature mostly bought the agent more time before the human review starts.\n\n"
                "Ralph Workflow is free and open-source: it runs that finish-first approach with the agents you already use."
            ),
            # Structure: constraint-first → three-item standard
            (
                "The three things a run-until-done feature needs to be trustworthy: finished code, proof the checks ran, and a named list of what still needs attention.\n\n"
                "Without all three, longer runtime just defers the review instead of removing it.\n\n"
                "Ralph Workflow is free and open-source: it applies that standard so longer runs come back trustworthy."
            ),
        ]
    elif any(token in opp.title.lower() for token in ["claude", "codex", "agent"]):
        fallback_bodies = [
            # Structure: specific failure story → fix
            (
                "The multi-tool failure I kept hitting: one session sounds confident, the next session inherits contradictory assumptions, and the combined result quietly stops making sense.\n\n"
                "The fix is an explicit baton pass between sessions — scoped task, bounded diff, check evidence, named open decisions.\n\n"
                "Ralph Workflow is free and open-source: it enforces that baton pass so sessions hand off cleanly."
            ),
            # Structure: constraint rule → concrete result
            (
                "The constraint that cleaned up our multi-agent runs: the second pass never re-implements. It only reviews and flags what the first pass left ambiguous.\n\n"
                "That separation alone removed most of the overlap confusion and made the morning result something I could actually grade.\n\n"
                "Ralph Workflow is free and open-source: it enforces that role separation so the morning result is reviewable."
            ),
            # Structure: outcome-first → bounded result standard
            (
                "The metric I use: can I open the repo in the morning and understand what changed in under two minutes?\n\n"
                "If the run ends with finished code, passed checks, and named open decisions — yes. If it ends with a paragraph — no.\n\n"
                "Ralph Workflow is free and open-source: it runs that bounded standard so the morning review is fast."
            ),
        ]
    else:
        fallback_bodies = [
            # Structure: specific mistake → what fixed it
            (
                "The mistake I had to stop making: treating the session transcript as the deliverable instead of the diff.\n\n"
                "Once the run ended with finished code, a diff I could read quickly, and named open decisions, the morning review became fast and the feedback loop actually closed.\n\n"
                "Ralph Workflow is free and open-source: it enforces that finish contract so the result is actually done."
            ),
            # Structure: tool observation → workflow change
            (
                "What I changed in my setup after the third overnight mess: every substantial run now produces a three-item receipt — what changed, what passed, and what still needs a name.\n\n"
                "That made the morning result something I could actually work with instead of something I had to reconstruct.\n\n"
                "Ralph Workflow is free and open-source: it enforces the three-item receipt so the morning result is reviewable."
            ),
            # Structure: concrete metric → bounded standard
            (
                "The finish standard I now enforce: a diff I can read in two minutes, test results that prove the checks ran, and a named list of open decisions no longer than a sentence.\n\n"
                "Without that three-item receipt, the run is not done — it is just paused.\n\n"
                "Ralph Workflow is free and open-source: it runs that finish standard so the result is actually done."
            ),
        ]

    fallback_bodies = one_paragraph_candidates(opp) + fallback_bodies

    candidates: list[str] = []
    for body in fallback_bodies:
        if should_add_github_link(opp):
            candidates.extend(build_github_link_candidates(body, opp))
        else:
            candidates.append(body)

    # 2026-05-19: hard-block banned phrases AND banned opening prefixes in emergency path.
    safe_candidates = [
        c for c in candidates
        if not contains_banned_phrase(c)
        and not opening_is_repetitive(c, recent)
        and not candidate_policy_issues(c, opp)
    ]
    if not safe_candidates:
        safe_candidates = [_last_resort_safe_body(opp)]

    rescored: list[tuple[float, int, str]] = []
    for candidate in safe_candidates:
        penalty, length_score = body_penalty(candidate, recent)
        if concept_cadence_repeats(candidate, recent):
            penalty += 0.75
        rescored.append((penalty, length_score, candidate))
    return sorted(rescored, key=lambda item: (item[0], item[1]))[0][2]


def build_comment(opp: Opportunity, recent: list[str], retro: dict | None = None) -> str:
    # Hard-block bodies with banned phrases — penalty alone is insufficient,
    # since a banned body can still win if all others are worse on other dimensions.
    # 2026-05-19: also hard-block banned opening prefixes — they bypass body_penalty
    # and can win the scoring if all other candidates are similarly penalized.
    # 2026-05-20: also hard-block bodies that were used in the last ~48 h,
    # even if the jsonl write is still pending from a prior rapid watchdog call.
    recent_hashes = recent_body_hashes()
    candidates = [
        c for c in comment_candidates(opp, retro=retro)
        if not contains_banned_phrase(c)
        and not opening_is_repetitive(c, recent)
        and _body_hash(c) not in recent_hashes
        and not validate_marketing_copy(c)
        and not candidate_policy_issues(c, opp)
    ]
    if not candidates:
        return emergency_rewrite(opp, recent=recent)
    scored: list[tuple[float, int, str]] = []
    for candidate in candidates:
        penalty, length_score = body_penalty(candidate, recent)
        if concept_cadence_repeats(candidate, recent):
            penalty += 0.75
        scored.append((penalty, length_score, candidate))
    best = sorted(scored, key=lambda item: (item[0], item[1]))[0][2]
    if body_needs_regeneration(best, recent):
        return emergency_rewrite(opp, recent=recent)
    if validate_marketing_copy(best):
        return emergency_rewrite(opp, recent=recent)
    if candidate_policy_issues(best, opp):
        return emergency_rewrite(opp, recent=recent)
    return best


def main() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = LOCK_PATH.open("x")
        fd.write("locked\n")
        fd.close()
    except FileExistsError:
        print(json.dumps({"ok": False, "status": "locked", "detail": str(LOCK_PATH)}, indent=2))
        return 1

    try:
        report = latest_report()
        state = load_state()
        last_status = state.get("last_attempt_status")
        recent_posts = load_recent_post_records(hours=24)
        allow_posting, gate_reason, retry_after_minutes, next_safe_post_at = posting_gate(datetime.now(), recent_posts)
        if not allow_posting:
            save_state({
                "last_attempt_at": __import__("datetime").datetime.now().isoformat(),
                "last_report": str(report),
                "last_attempt_status": "cooldown_skip",
                "last_detail": gate_reason,
                "retry_after_minutes": retry_after_minutes,
                "next_safe_post_at": next_safe_post_at,
            })
            print(json.dumps({
                "ok": True,
                "status": "cooldown_skip",
                "report": str(report),
                "detail": gate_reason,
                "retry_after_minutes": retry_after_minutes,
                "next_safe_post_at": next_safe_post_at,
            }, indent=2))
            return 0
        retro = refresh_retrospective()
        repair_reasons = reddit_lane_repair_reasons(retro=retro)
        if repair_reasons:
            save_state({
                "last_attempt_at": __import__("datetime").datetime.now().isoformat(),
                "last_report": str(report),
                "last_attempt_status": "repair_blocked",
                "last_detail": "; ".join(repair_reasons),
            })
            print(json.dumps({
                "ok": True,
                "status": "repair_blocked",
                "report": str(report),
                "detail": repair_reasons,
                "next_action": "shift_distribution_lane_away_from_reddit",
            }, indent=2))
            return 0
        text = report.read_text(encoding="utf-8")
        opps = parse_opportunities(text)
        report_guard_reasons = report_posting_guard(text, opps)
        if state.get("last_report") == str(report) and last_status in {"posted", "already_consumed", "already_logged"}:
            print(json.dumps({"ok": True, "status": "already_consumed", "report": str(report), "last_comment_url": state.get("last_comment_url")}, indent=2))
            return 0
        if state.get("last_report") == str(report) and last_status == "no_unused_opportunity" and not opps:
            print(json.dumps({"ok": True, "status": "already_consumed", "report": str(report), "last_comment_url": state.get("last_comment_url")}, indent=2))
            return 0
        if report_guard_reasons and "mention_fit_below_medium" in report_guard_reasons:
            save_state({
                "last_attempt_at": __import__("datetime").datetime.now().isoformat(),
                "last_report": str(report),
                "last_attempt_status": "weak_fit_only_skip",
                "last_detail": "; ".join(report_guard_reasons),
            })
            print(json.dumps({
                "ok": True,
                "status": "weak_fit_only_skip",
                "report": str(report),
                "detail": report_guard_reasons,
                "opportunities": len(opps),
            }, indent=2))
            return 0

        chosen, opportunity_state = choose_opportunity(opps)
        if not chosen:
            status_map = {
                "fresh_rate_limited": "fresh_opportunity_rate_limited",
                "weak_fit_only": "weak_fit_only_skip",
                "stale_only": "stale_only_skip",
                "fully_consumed": "no_unused_opportunity",
            }
            status = status_map.get(opportunity_state, "no_unused_opportunity")
            save_state({
                "last_attempt_at": __import__("datetime").datetime.now().isoformat(),
                "last_report": str(report),
                "last_attempt_status": status,
                "last_detail": f"opportunity_state:{opportunity_state}; opportunities:{len(opps)}",
            })
            print(json.dumps({"ok": True, "status": status, "report": str(report), "opportunities": len(opps)}, indent=2))
            return 0

        recent = recent_bodies()
        body = build_comment(chosen, recent=recent, retro=retro)
        positioning_issues = validate_marketing_copy(body)
        if positioning_issues:
            save_state({
                "last_attempt_at": __import__("datetime").datetime.now().isoformat(),
                "last_report": str(report),
                "last_attempt_status": "positioning_blocked",
                "last_detail": "; ".join(positioning_issues),
            })
            print(json.dumps({
                "ok": False,
                "status": "positioning_blocked",
                "report": str(report),
                "issues": positioning_issues,
            }, indent=2))
            return 1
        if body_needs_regeneration(body, recent):
            # 2026-05-19: also filter by banned opening prefixes in fallback path.
            fallback_variants = [
                candidate for candidate in comment_candidates(chosen, retro=retro)
                if not body_needs_regeneration(candidate, recent)
                and not opening_is_repetitive(candidate, recent)
                and _body_hash(candidate) not in recent_hashes
            ]
            if fallback_variants:
                rescored: list[tuple[float, int, str]] = []
                for candidate in fallback_variants:
                    penalty, length_score = body_penalty(candidate, recent)
                    rescored.append((penalty, length_score, candidate))
                body = sorted(rescored, key=lambda item: (item[0], item[1]))[0][2]
            else:
                body = emergency_rewrite(chosen, recent=recent)
        body_file = OUT_DIR / "reddit_autopost_comment.txt"
        body_file.write_text(body + "\n", encoding="utf-8")
        note = f"Autoposted from reddit-monitor shortlist: #{chosen.rank} {chosen.title} ({chosen.community})."
        metadata = json.dumps({
            "report": str(report),
            "rank": chosen.rank,
            "title": chosen.title,
            "community": chosen.community,
            "angle": chosen.angle,
            "mention_fit": chosen.mention_fit,
        })

        # Prefer PRAW (Reddit API) over Playwright when credentials are available
        if _praw_available():
            cmd = [sys.executable, str(PRAW_POSTER), chosen.url, "--body-file", str(body_file)]
        else:
            cmd = [sys.executable, str(POSTER), chosen.url, "--body-file", str(body_file), "--note", note, "--metadata-json", metadata]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            poster_payload = json.loads(result.stdout) if result.stdout.strip() else {}
        except Exception:
            poster_payload = {}
        poster_status = poster_payload.get("status")
        if result.returncode == 0:
            mark_body_used(body)
        payload = {
            "ok": result.returncode == 0,
            "status": "posted" if result.returncode == 0 else (poster_status or "failed"),
            "report": str(report),
            "chosen": chosen.__dict__,
            "body_file": str(body_file),
            "poster_stdout": result.stdout.strip(),
            "poster_stderr": result.stderr.strip(),
        }
        save_state({
            "last_attempt_at": __import__("datetime").datetime.now().isoformat(),
            "last_report": str(report),
            "last_thread_url": chosen.url,
            "last_comment_url": poster_payload.get("comment_url"),
            "last_attempt_status": payload["status"],
            "last_poster_status": poster_status,
            "last_detail": poster_payload.get("detail") or result.stderr.strip(),
        })
        print(json.dumps(payload, indent=2))
        return result.returncode
    finally:
        LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
