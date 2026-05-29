#!/usr/bin/env python3
"""ARCHITECTURALLY RETIRED 2026-05-28 — prepares packets for a permanently-blocked channel.
No-op.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

if __name__ == '__main__':
    print(json.dumps({'status': 'retired', 'reason': 'Reddit pipeline architecturally retired 2026-05-28'}))
    sys.exit(0)

ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing import reddit_autopost
DRAFTS_DIR = ROOT / "drafts"
STATE_PATH = ROOT / "agents/marketing/logs/reddit_autopost_state.json"
LATEST_PATH = DRAFTS_DIR / "reddit_next_window_packets_latest.md"


@dataclass
class PacketEntry:
    opportunity: reddit_autopost.Opportunity
    landing_page: str
    why: list[str]
    body: str


LANDING_PAGES = {
    "overnight": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/when-unattended-coding-fits.md",
    "trust": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/review-ai-coding-output-before-merge.md",
    "workflow": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/review-ai-coding-output-before-merge.md",
    "handoff": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/claude-code-codex-workflow.md",
    "codex": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/claude-code-codex-workflow.md",
    "mixed_team": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/claude-code-codex-workflow.md",
    "breaks_first": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/what-a-good-ai-coding-finish-receipt-looks-like.md",
    "remote": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/what-a-good-ai-coding-finish-receipt-looks-like.md",
    "generic": "https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/START_HERE.md",
}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cleaned(text: str) -> str:
    return " ".join((text or "").replace("**", "").split())


def landing_page_for(opp: reddit_autopost.Opportunity) -> str:
    category = reddit_autopost.detect_category(opp.title)
    return LANDING_PAGES.get(category, LANDING_PAGES["generic"])


def why_lines_for(opp: reddit_autopost.Opportunity, landing_page: str) -> list[str]:
    category = reddit_autopost.detect_category(opp.title)
    angle = cleaned(opp.angle)
    lines = []
    if category == "overnight":
        lines.extend([
            "direct unattended-run thread with pain around scope, drift, and boring morning-after review",
            "best chance to seed task-fit language instead of generic autonomy hype",
        ])
    elif category in {"trust", "workflow"}:
        lines.extend([
            "pain is already about approval drag, review surface, or what is actually safe to merge",
            "landing page keeps the reply anchored to proof and finish-state clarity",
        ])
    elif category in {"handoff", "codex", "mixed_team"}:
        lines.extend([
            "thread naturally fits builder/reviewer phase boundaries and handoff discipline",
            "landing page explains why mixed-agent flow only matters when the finish stays inspectable",
        ])
    elif category in {"remote", "breaks_first"}:
        lines.extend([
            "pain is really about re-entry and visible finish state, not more live supervision",
            "landing page gives a concrete receipt standard instead of abstract coordination talk",
        ])
    else:
        lines.extend([
            "still usable as a workflow reply without forcing a product pitch",
            "landing page keeps the CTA on a concrete first-use or proof path",
        ])
    if angle:
        lines.append(f"best RalphWorkflow angle from the monitor: {angle}")
    return lines


def load_recent_bodies(limit: int = 12) -> list[str]:
    records = reddit_autopost.load_recent_post_records(hours=168)
    bodies = [record.get("body", "") for record in records if record.get("body")]
    return bodies[-limit:]



def draft_body_for(
    opp: reddit_autopost.Opportunity,
    landing_page: str,
    recent_bodies: list[str] | None = None,
) -> str:
    recent_bodies = recent_bodies or []
    body = reddit_autopost.build_comment(opp, recent=recent_bodies)
    if landing_page in body:
        return body
    if "http://" in body or "https://" in body:
        return body
    return f"{body}\n\n{landing_page}"


def rank_opportunities(opps: list[reddit_autopost.Opportunity]) -> list[reddit_autopost.Opportunity]:
    candidates = []
    for opp in opps:
        if reddit_autopost.already_used(opp.url):
            continue
        fit = reddit_autopost.mention_fit_score(opp.mention_fit)
        if fit <= 0:
            continue
        candidates.append(opp)
    return sorted(
        candidates,
        key=lambda opp: (
            -reddit_autopost.mention_fit_score(opp.mention_fit),
            -reddit_autopost.finish_surface_score(opp),
            -reddit_autopost.freshness_score(opp.freshness),
            opp.rank,
        ),
    )


def build_packet(report: Path, max_entries: int = 3) -> tuple[str, list[PacketEntry]]:
    state = load_state()
    text = report.read_text(encoding="utf-8")
    opps = reddit_autopost.parse_opportunities(text)
    ranked = rank_opportunities(opps)[:max_entries]

    recent_bodies = load_recent_bodies()
    entries: list[PacketEntry] = []
    for opp in ranked:
        landing_page = landing_page_for(opp)
        body = draft_body_for(opp, landing_page, recent_bodies=recent_bodies)
        entries.append(
            PacketEntry(
                opportunity=opp,
                landing_page=landing_page,
                why=why_lines_for(opp, landing_page),
                body=body,
            )
        )
        recent_bodies.append(body)

    now = datetime.now().astimezone()
    lines = [
        f"# RalphWorkflow Reddit next-window packet - {now.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "Live posting is **not** the move right now.",
        "",
        f"Latest watchdog state references report `{report.name}`.",
    ]
    if state.get("last_attempt_status"):
        lines.append(f"- `status: {state.get('last_attempt_status')}`")
    if state.get("last_detail"):
        lines.append(f"- `detail: {state.get('last_detail')}`")
    if state.get("retry_after_minutes") is not None:
        lines.append(f"- `retry_after_minutes: {state.get('retry_after_minutes')}`")
    if state.get("next_safe_post_at"):
        lines.append(f"- `next_safe_post_at: {state.get('next_safe_post_at')}`")
    lines.extend([
        "",
        "So this packet is optimized for the **next safe window**, not for forcing a post now.",
        "",
        "Use these only if the next watchdog check clears the rate window and the thread still looks fresh enough.",
        "",
        "Messaging guardrails preserved in every draft:",
        "- what it is: Ralph Workflow is a free and open-source tool that orchestrates the coding agents you already use on your own machine",
        "- who it is for: developers doing engineering work too big to babysit and too risky to trust blindly",
        "- why different: it is about a reviewable finish state, not just more agent sessions",
        "- why now: you can use it tonight for overnight work and wake up to something you can actually review",
        "",
        "---",
        "",
    ])

    if not entries:
        lines.extend([
            "No medium-or-better unused opportunities were available from this report.",
            "",
            "Use the next safe window for a fresh monitor pass instead of forcing a weak-fit reply.",
        ])
        return "\n".join(lines) + "\n", entries

    for idx, entry in enumerate(entries, start=1):
        opp = entry.opportunity
        lines.extend([
            f"## {idx}) {opp.community} - {opp.title}",
            f"- URL: {opp.url}",
            f"- Mention fit: {cleaned(opp.mention_fit) or 'n/a'}",
            f"- Best landing page to seed:\n  - {entry.landing_page}",
            "- Why this stays in the packet:",
        ])
        for why in entry.why:
            lines.append(f"  - {why}")
        lines.extend([
            "",
            f"### Draft body {chr(64 + idx)}",
            entry.body,
            "",
            "---",
            "",
        ])

    lines.extend([
        "## Posting discipline before using any of these",
        "1. Re-read the last 3 logged Reddit bodies first.",
        "2. Re-run `python3 agents/marketing/reddit_watchdog.py` before posting; do not trust this file alone on timing.",
        "3. If the thread shifted, rewrite the opener instead of forcing the draft.",
        "4. If the reply is useful without Ralph, keep Ralph secondary.",
        "5. Use one seeded proof/comparison link only - no link pile.",
        "6. Prefer only one post in the next safe window unless a second thread is clearly exceptional.",
        "7. Reject any draft that falls back into the same cadence as the last three bodies.",
    ])

    return "\n".join(lines) + "\n", entries


def output_path(now: datetime | None = None) -> Path:
    now = now or datetime.now().astimezone()
    return DRAFTS_DIR / f"{now.strftime('%Y-%m-%d')}_reddit_next_window_packets.md"


def main() -> int:
    # ── Spidering guard: Reddit is permanently blocked — no packet generation ──
    try:
        from agents.marketing.channel_spidering_guard import guard_check, guard_record
        allowed, reason, remaining = guard_check("reddit")
        if not allowed:
            guard_record("reddit", ok=False, fingerprint="spidering_guard_rejected")
            print(json.dumps({"ok": False, "status": "spidering_blocked", "reason": reason, "live_external_action": False}))
            return 1
    except ImportError:
        pass

    report = reddit_autopost.latest_report()
    packet, entries = build_packet(report)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = output_path()
    dated_path.write_text(packet, encoding="utf-8")
    LATEST_PATH.write_text(packet, encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "status": "packet_generated",
        "report": str(report),
        "entries": len(entries),
        "paths": [str(dated_path), str(LATEST_PATH)],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
