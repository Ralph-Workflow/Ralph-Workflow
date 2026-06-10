#!/usr/bin/env python3
"""distribution_lane_selector.py — clean reimplementation, rebuilt 2026-06-10 (D23).

Original (~4,700 lines) was lost when a deletion event (defect D23, see
APOLLO_DEFECT_CLASSES.md) wiped the distribution-lane module family; run.py and
outcome_execution_board_runner crashed on import for ~2 days before the fleet
monitor caught it. This is NOT the bloated original and NOT the recovery shim: it
is a genuinely functional, intentionally small selector sized to exactly what the
callers (run.py, outcome_execution_board_runner.py, marketing_workflow_audit.py,
tests/) consume.

What it does: sense real on-disk marketing state (adoption metrics, the curator /
comparison / backlink queues, owned-content recency, the execution board) and pick
ONE distribution lane with explicit, commented rules. It then persists a human-
readable action brief + the latest-lane JSON/MD aliases the rest of the loop reads.

Canon honored (MARKETING_COVERAGE_MAP.md, APOLLO_DEFECT_CLASSES.md D07/D08/D15):
this module only SELECTS a lane and writes briefs; it never emits outbound copy, so
it cannot violate OUTREACH_COPY_CONTRACT.md. The executor performs the safe side of
each lane and is the only place a live external action could ever originate.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict, is_dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

MKT = Path(__file__).resolve().parent
ROOT = MKT.parent.parent
LOG_DIR = MKT / "logs"
LOGS = LOG_DIR  # back-compat alias
DRAFTS_DIR = ROOT / "drafts"

LATEST_JSON = LOG_DIR / "distribution_lane_latest.json"
LATEST_MD = LOG_DIR / "distribution_lane_latest.md"

ADOPTION_PATH = LOG_DIR / "adoption_metrics_latest.json"
CHANNEL_DISCOVERY_PATH = LOG_DIR / "channel_discovery.json"
COMPARISON_QUEUE_LATEST_PATH = LOG_DIR / "comparison_backlink_queue_latest.json"
CURATOR_QUEUE_LATEST_PATH = LOG_DIR / "curator_outreach_queue_latest.json"
BACKLINK_STATUS_LATEST_PATH = LOG_DIR / "backlink_status_latest.json"
AUDIT_LATEST_PATH = LOG_DIR / "marketing_workflow_audit_latest.json"
EXECUTION_BOARD_LATEST = DRAFTS_DIR / "marketing_execution_board_latest.md"

# The post-execution review window the loop holds for before re-acting. The original
# used 6h; run.py / outcome_execution_board_runner read this off the module.
SHORT_REVIEW_WINDOW_HOURS = 6

# Active audit repair-window statuses that mean "the loop should not pretend a lane is
# fresh just to fill the slot".
ACTIVE_REPAIR_WINDOW_STATUSES = {"needs_repair", "measurement_pending"}

# Explicit empty-board markers the executor writes when no do-now packet is truthful.
_EMPTY_BOARD_MARKERS = (
    "No do-now handoff packet is currently truthful in this review window.",
    "No truthful do-now packet remains on this board right now.",
    "- None in the current short-window hold.",
    "- targets: none",
)


@dataclass(frozen=True)
class LaneDecision:
    """The selection result every caller consumes (frozen; run.py mutates copies via
    object.__setattr__ / dataclasses.replace, matching the original contract)."""

    lane: str = "distribution_architecture_guard_pause"
    reason: str = ""
    reasons: list = field(default_factory=list)
    owned_content_posts_last_36h: int = 0
    unsubmitted_directory_channels: list = field(default_factory=list)
    shared_findings_used: list = field(default_factory=list)
    artifact_path: str = ""
    short_review_window_release_at: str | None = None
    # Repair-awareness flags set by run.py / board runner when audit repairs are pending.
    skip_directory_submissions: bool = False
    skip_curator_outreach: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# small IO helpers
# ─────────────────────────────────────────────────────────────────────────────
def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


# ─────────────────────────────────────────────────────────────────────────────
# execution-board sensing (consumed by run.py + outcome_execution_board_runner)
# ─────────────────────────────────────────────────────────────────────────────
def _execution_board_text() -> str:
    try:
        return EXECUTION_BOARD_LATEST.read_text(encoding="utf-8")
    except OSError:
        return ""


def _normalized_execution_board_text(text: str) -> str:
    """Drop the volatile Generated: timestamp so the fingerprint only changes when the
    board's *content* changes (prevents fake-progress churn — D08 lesson)."""
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("Generated:")]
    return "\n".join(ln.rstrip() for ln in lines).strip()


def _execution_board_fingerprint() -> str:
    normalized = _normalized_execution_board_text(_execution_board_text())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _execution_board_has_no_truthful_do_now_packet(now: datetime | None = None) -> bool:
    """True when the board carries no do-now packet the loop can truthfully act on.

    Conservative: an explicit empty-state marker OR a board that has no "When: Do now"
    block at all both count as "no truthful packet". A board that surfaces a live
    do-now asset returns False so callers act on it instead of collapsing to a hold.
    """
    text = _execution_board_text()
    if not text:
        return True
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in _EMPTY_BOARD_MARKERS):
        return True
    # A real do-now packet is a "When: Do now" line under an asset block.
    return "when: do now" not in lowered


def _execution_board_short_review_release_at() -> datetime | None:
    marker = "Short review-window congestion clears at:"
    for line in _execution_board_text().splitlines():
        if marker in line:
            return _parse_dt(line.split(marker, 1)[1].strip().lstrip("-").strip())
    return None


# ─────────────────────────────────────────────────────────────────────────────
# adoption / channel / queue sensing
# ─────────────────────────────────────────────────────────────────────────────
def _is_primary_repo_flat(adoption: dict[str, Any]) -> bool:
    evaluation = adoption.get("evaluation", {}) if isinstance(adoption, dict) else {}
    return "primary_repo_flat" in (evaluation.get("failing_signals", []) or [])


def _normalize_name(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _working_directory_channels() -> list[str]:
    payload = _load_json(CHANNEL_DISCOVERY_PATH)
    channels: list[str] = []
    for entry in payload.get("working", []) or []:
        name = str((entry or {}).get("name", "")).strip()
        if name:
            channels.append(_normalize_name(name))
    return channels


def _live_directory_listings() -> list[str]:
    """Directory listings the backlink tracker has verified as live on the public web."""
    payload = _load_json(BACKLINK_STATUS_LATEST_PATH)
    live: list[str] = []
    for name, info in (payload.get("directories", {}) or {}).items():
        if isinstance(info, dict) and info.get("listing_live"):
            live.append(str(name))
    return live


def _already_submitted_directory_channels() -> set[str]:
    """Channels that already have a live listing OR a recorded submission note."""
    payload = _load_json(BACKLINK_STATUS_LATEST_PATH)
    done: set[str] = set()
    for name, info in (payload.get("directories", {}) or {}).items():
        if not isinstance(info, dict):
            continue
        note = str(info.get("status_note") or "").lower()
        if info.get("listing_live") or "submitted" in note or "in-review" in note:
            done.add(_normalize_name(name))
    return done


def _unsubmitted_directory_channels() -> list[str]:
    submitted = _already_submitted_directory_channels()
    return sorted(c for c in _working_directory_channels() if c not in submitted)


def _comparison_queue_prepared_count() -> int:
    payload = _load_json(COMPARISON_QUEUE_LATEST_PATH)
    return sum(
        1
        for row in (payload.get("targets", []) or [])
        if isinstance(row, dict) and str(row.get("status") or "").strip().lower() == "prepared"
    )


def _due_curator_followup_targets(now: datetime) -> list[str]:
    """Curator queue rows whose review window has matured (sent/waiting, due <= now)."""
    payload = _load_json(CURATOR_QUEUE_LATEST_PATH)
    due: list[str] = []
    for row in payload.get("targets", []) or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in {"prepared", "draft"}:
            continue
        review_due = _parse_dt(row.get("review_due_date"))
        target = str(row.get("target") or row.get("name") or "").strip()
        if target and review_due is not None and review_due <= now:
            due.append(target)
    return due


def _github_auth_available() -> bool:
    import subprocess

    try:
        return subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, check=False, timeout=15
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _comparison_backlink_lane_manual_only_blocked(
    now: datetime | None = None, *, github_auth_available: bool | None = None
) -> bool:
    """True when comparison-backlink outreach can only be delivered MANUALLY (no auto
    live PR path), so run.py must not treat it as a fresh live lane.

    Blocked if the spidering guard permanently bans it, or if GitHub auth is missing
    while a prepared comparison queue is already waiting (manual follow-through only).
    """
    try:
        from agents.marketing.channel_spidering_guard import PERMANENTLY_BLOCKED

        if "comparison_backlink" in PERMANENTLY_BLOCKED:
            return True
    except ImportError:
        pass
    if github_auth_available is None:
        github_auth_available = _github_auth_available()
    if github_auth_available:
        return False
    # No live PR path + a prepared queue already exists -> manual-only.
    return _comparison_queue_prepared_count() > 0


def _directory_secondary_surface_followup_window() -> dict[str, Any]:
    """Latest scheduled re-check window for a directory secondary-surface repair.

    Read by outcome_execution_board_runner (and patched in tests). Returns {} when no
    such repair has been logged.
    """
    latest: dict[str, Any] = {}
    latest_dt: datetime | None = None
    for path in LOG_DIR.glob("marketing_*saashub*listing*correction*.json"):
        payload = _load_json(path)
        if str(payload.get("action") or "").strip() != "saashub_live_listing_correction":
            continue
        review_at = _parse_dt(str(payload.get("review_window") or "").strip())
        if review_at is None:
            continue
        dt = _parse_dt(payload.get("timestamp") or payload.get("timestamp_utc"))
        if dt is None:
            try:
                dt = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest = {"path": str(path), "timestamp": dt, "review_at": review_at}
    return latest


def _recent_owned_content_posts(now: datetime, hours: int = 36) -> list[dict[str, Any]]:
    """Successful owned-content publications in the trailing window (recency input)."""
    cutoff = now - timedelta(hours=hours)
    posts: list[dict[str, Any]] = []
    for path in LOG_DIR.glob("marketing_*.json"):
        if any(tok in path.name for tok in ("latest", "workflow_audit", "loop_runner")):
            continue
        payload = _load_json(path)
        chosen = payload.get("chosen_action") or {}
        result = payload.get("result") or {}
        if chosen.get("type") != "owned_content_publication" or not result.get("ok"):
            continue
        dt = _parse_dt(payload.get("timestamp") or payload.get("timestamp_utc"))
        if dt is None:
            try:
                dt = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
        if dt >= cutoff:
            posts.append(payload)
    return posts


def _shared_findings() -> list[str]:
    """The canonical ground-truth artifacts each decision reuses (never fabricated)."""
    findings = []
    if ADOPTION_PATH.exists():
        findings.append("adoption_metrics_latest.json: Codeberg movement is the primary success gate")
    if (LOG_DIR / "market_intelligence_latest.json").exists():
        findings.append("market_intelligence_latest.json: reusable competitor comparisons and positioning truths")
    if CHANNEL_DISCOVERY_PATH.exists():
        findings.append("channel_discovery.json: validated easy-submit directory lanes")
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# core selection
# ─────────────────────────────────────────────────────────────────────────────
def choose_distribution_lane(
    now: datetime | None = None,
    *,
    write_action_log: bool = True,
    persist_latest_artifacts: bool = True,
) -> LaneDecision:
    """Pick one distribution lane from real sensed state, with explicit rules.

    Lane vocabulary (from the historical artifacts):
      owned_content_amplification / owned_content  — ship/refresh an owned asset
      directory_submission                         — net-new easy-submit directory work
      comparison_backlink_outreach                 — comparison/backlink follow-through
      distribution_architecture_repair             — empty board + flat primary repo
      distribution_architecture_guard_pause        — nothing truthful to do; hold safely
    """
    now = now or datetime.now()

    adoption = _load_json(ADOPTION_PATH)
    audit = _load_json(AUDIT_LATEST_PATH)
    primary_flat = _is_primary_repo_flat(adoption)
    board_empty = _execution_board_has_no_truthful_do_now_packet(now)
    unsubmitted = _unsubmitted_directory_channels()
    comparison_prepared = _comparison_queue_prepared_count()
    comparison_manual_only = _comparison_backlink_lane_manual_only_blocked(now)
    due_curators = _due_curator_followup_targets(now)
    recent_owned = _recent_owned_content_posts(now)
    shared = _shared_findings()
    release_dt = _execution_board_short_review_release_at()
    release_at = release_dt.isoformat(timespec="seconds") if release_dt else None

    reasons: list[str] = []
    if primary_flat:
        reasons.append("Primary Codeberg adoption is flat in the current measurement window.")

    # RULE 1 — Empty board while the primary repo is flat (or the audit bottleneck is
    # conversion/distribution): the highest-leverage move is to repair the distribution
    # architecture, not stack another reset or hold. (Anti-theater, D15: work the
    # binding constraint.)
    bottleneck = str(audit.get("current_bottleneck") or "").strip().lower()
    conversion_bottleneck = bottleneck in {
        "conversion_to_free_use",
        "distribution_and_message_to_primary_repo_conversion",
    }
    if board_empty and (primary_flat or conversion_bottleneck):
        reasons.append(
            "Execution board has no truthful do-now packet and the primary repo is flat, "
            "so repair the distribution architecture instead of idling on a hold."
        )
        return _finalize(
            "distribution_architecture_repair",
            "Empty execution board + flat primary repo: choose distribution_architecture_repair "
            "to produce a changed truthful lane/blocker state rather than another measurement hold.",
            reasons, recent_owned, unsubmitted, shared, release_at,
            now, write_action_log, persist_latest_artifacts,
        )

    # RULE 2 — Net-new directory submissions: real, low-cost demand-capture surfaces the
    # backlink tracker hasn't recorded yet. Prefer these before re-planning.
    if unsubmitted:
        reasons.append(
            f"{len(unsubmitted)} working directory channel(s) have no recorded submission yet "
            f"({', '.join(unsubmitted[:3])}), so prepare a net-new submission packet."
        )
        return _finalize(
            "directory_submission",
            "Net-new easy-submit directory channels are still unsubmitted; prepare that "
            "Codeberg-primary submission work before falling back to a hold.",
            reasons, recent_owned, unsubmitted, shared, release_at,
            now, write_action_log, persist_latest_artifacts,
        )

    # RULE 3 — Comparison/backlink follow-through. If a prepared queue exists and it is
    # manual-only blocked (no live PR path), surface it as a follow-through lane so the
    # owner can deliver it — never auto-submit.
    if comparison_prepared > 0:
        if comparison_manual_only:
            reasons.append(
                f"{comparison_prepared} comparison/backlink target(s) are prepared but the live "
                "PR path is blocked (GitHub auth), so this is manual-only follow-through."
            )
        else:
            reasons.append(
                f"{comparison_prepared} comparison/backlink target(s) are prepared and a live "
                "submission path is available."
            )
        return _finalize(
            "comparison_backlink_outreach",
            "A prepared comparison/backlink queue is waiting; surface Codeberg-primary "
            "comparison follow-through instead of inventing new reset work.",
            reasons, recent_owned, unsubmitted, shared, release_at,
            now, write_action_log, persist_latest_artifacts,
        )

    # RULE 4 — Owned-content amplification. If nothing external is open and we have NOT
    # already shipped an owned asset in the recency window, refresh/amplify owned media
    # (site/repo/blog) — a real artifact rather than a hold.
    if not recent_owned:
        reasons.append("No owned-content asset shipped in the last 36h; amplify owned media.")
        return _finalize(
            "owned_content_amplification",
            "No external lane is open and no owned asset shipped recently; amplify owned "
            "media (site/repo/blog) to ship a real Codeberg-primary artifact this run.",
            reasons, recent_owned, unsubmitted, shared, release_at,
            now, write_action_log, persist_latest_artifacts,
        )

    # RULE 5 — Safe guard pause. Everything truthful is already in flight, already
    # delivered, or blocked; hold without firing any automatic action and wait for a
    # changed board fingerprint or a matured review window.
    if due_curators:
        reasons.append(
            f"{len(due_curators)} curator review window(s) are due; let the board runner "
            "surface them rather than opening a fresh reset lane."
        )
    reasons.append("No untouched truthful lane is open right now; pause guard churn until state changes.")
    return _finalize(
        "distribution_architecture_guard_pause",
        "All truthful lanes are in flight, delivered, or blocked: hold a guard pause and "
        "wait for a changed board fingerprint, blocker set, or matured review window.",
        reasons, recent_owned, unsubmitted, shared, release_at,
        now, write_action_log, persist_latest_artifacts,
    )


def _finalize(
    lane: str,
    reason: str,
    reasons: list[str],
    recent_owned: list[dict[str, Any]],
    unsubmitted: list[str],
    shared: list[str],
    release_at: str | None,
    now: datetime,
    write_action_log: bool,
    persist_latest_artifacts: bool,
) -> LaneDecision:
    decision = LaneDecision(
        lane=lane,
        reason=reason,
        reasons=reasons,
        owned_content_posts_last_36h=len(recent_owned),
        unsubmitted_directory_channels=unsubmitted,
        shared_findings_used=shared,
        artifact_path="",
        short_review_window_release_at=release_at,
    )
    if persist_latest_artifacts:
        return persist_latest_lane_decision(decision, now, write_action_log=write_action_log)
    return decision


# ─────────────────────────────────────────────────────────────────────────────
# artifact persistence (latest JSON/MD aliases + action brief)
# ─────────────────────────────────────────────────────────────────────────────
def write_action_brief(
    *,
    lane: str,
    now: datetime,
    reason: str,
    reasons: list[str],
    shared_findings: list[str],
    unsubmitted_channels: list[str],
    recent_posts: list[dict[str, Any]],
    write_latest_md: bool = True,
) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    artifact = DRAFTS_DIR / f"{now.strftime('%Y-%m-%d')}_distribution_action_brief.md"

    lines = [
        "# Ralph Workflow Distribution Action Brief",
        f"Generated: {now.isoformat(timespec='seconds')}",
        f"Chosen lane: **{lane}**",
        "",
        "## Why this lane",
        f"- {reason}",
    ]
    lines.extend(f"- {item}" for item in reasons)
    if shared_findings:
        lines.extend(["", "## Shared findings reused"])
        lines.extend(f"- {item}" for item in shared_findings)
    if unsubmitted_channels:
        lines.extend(["", "## Unsubmitted directory channels"])
        lines.extend(f"- {item}" for item in unsubmitted_channels)
    if recent_posts:
        lines.extend(["", "## Recent owned-content already shipped"])
        for post in recent_posts[-3:]:
            chosen = post.get("chosen_action") or {}
            lines.append(f"- {chosen.get('title') or chosen.get('draft') or 'owned-content publication'}")
    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if write_latest_md:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        LATEST_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return artifact


def persist_latest_lane_decision(
    decision: Any,
    now: datetime | None = None,
    *,
    write_action_log: bool = False,
) -> LaneDecision:
    """Write the action brief + latest JSON/MD aliases and return the persisted decision.

    run.py and outcome_execution_board_runner call this positionally as
    (decision, now, write_action_log=False) and consume the RETURN value's .lane /
    .artifact_path, so the contract is: write artifacts, return a LaneDecision whose
    artifact_path points at today's brief.
    """
    now = now or datetime.now()

    lane = str(getattr(decision, "lane", "") or "distribution_architecture_guard_pause")
    reason = str(getattr(decision, "reason", "") or "")
    reasons = list(getattr(decision, "reasons", []) or [])
    shared = list(getattr(decision, "shared_findings_used", []) or [])
    unsubmitted = list(getattr(decision, "unsubmitted_directory_channels", []) or [])

    artifact_path = str(
        write_action_brief(
            lane=lane,
            now=now,
            reason=reason,
            reasons=reasons,
            shared_findings=shared,
            unsubmitted_channels=unsubmitted,
            recent_posts=_recent_owned_content_posts(now),
            write_latest_md=True,
        )
    )

    if is_dataclass(decision) and not isinstance(decision, type):
        persisted = replace(decision, artifact_path=artifact_path)
    else:
        persisted = LaneDecision(
            lane=lane,
            reason=reason,
            reasons=reasons,
            owned_content_posts_last_36h=int(getattr(decision, "owned_content_posts_last_36h", 0) or 0),
            unsubmitted_directory_channels=unsubmitted,
            shared_findings_used=shared,
            artifact_path=artifact_path,
            short_review_window_release_at=getattr(decision, "short_review_window_release_at", None),
            skip_directory_submissions=bool(getattr(decision, "skip_directory_submissions", False)),
            skip_curator_outreach=bool(getattr(decision, "skip_curator_outreach", False)),
        )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(asdict(persisted), indent=2) + "\n", encoding="utf-8")
    return persisted
