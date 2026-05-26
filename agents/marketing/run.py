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
from types import SimpleNamespace
from typing import Any, Iterable

ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing import distribution_lane_selector, outcome_execution_board_runner
from agents.marketing.distribution_lane_executor import (
    execute_distribution_lane,
    _schedule_measurement_hold_release_run,
    _write_marketing_execution_board,
    _write_post_hold_reentry_contract,
)
from agents.marketing.distribution_lane_selector import choose_distribution_lane
from agents.marketing.market_intelligence_runtime import load_market_intelligence
from agents.marketing.measurement_hold_runtime import latest_measurement_hold_window as shared_latest_measurement_hold_window

AGENTS_DIR = ROOT / "agents/marketing"
LOG_DIR = AGENTS_DIR / "logs"
STRATEGY_FILE = AGENTS_DIR / "STRATEGY.md"
POSTED_FILE = LOG_DIR / "posted_urls.json"
SEO_REPORTS_DIR = ROOT / "seo-reports"
DRAFTS_DIR = ROOT / "drafts"
ADOPTION_FILE = LOG_DIR / "adoption_metrics_latest.json"
MARKET_INTELLIGENCE_FILE = LOG_DIR / "market_intelligence_latest.json"
PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH = LOG_DIR / "primary_repo_flat_contact_discovery_latest.json"
PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_SCRIPT_PATH = AGENTS_DIR / "primary_repo_flat_contact_discovery.py"
PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_STALE_AFTER = timedelta(hours=6)
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

STRUCTURAL_REPLACEMENT_ACTION_TYPES = {
    "apollo_outreach_execution",
}

SYSTEM_DESIGN_REPAIR_ACTION_TYPES = {
    "distribution_architecture_repair",
    "distribution_architecture_churn_guard_repair",
    "measurement_hold_churn_guard_repair",
    "measurement_hold_release_reschedule_repair",
    "post_hold_release_prompt_guard_repair",
    "measurement_hold_release_delivery_route_repair",
    "apollo_truthfulness_repair",
    "apollo_cloudflare_truthfulness_repair",
    "apollo_runtime_truth_repair",
    "apollo_followup_truth_repair",
}

DIRECTORY_LANE_ACTION_TYPES = {
    "directory_submission_execution",
}

CURATOR_LANE_ACTION_TYPES = {
    "curator_outreach_execution",
    "curator_queue_follow_through",
    "curator_handoff_packet_execution",
    "curator_handoff_follow_through",
    "curator_contact_handoff_packet_execution",
    "curator_contact_handoff_follow_through",
}

MANUAL_CONTACT_REPAIR_ACTION_TYPES = {
    "curator_contact_handoff_packet_execution",
}

PUBLISHER_LANE_ACTION_TYPES = {
    "primary_repo_flat_contact_handoff_packet_execution",
    "primary_repo_flat_contact_handoff_follow_through",
    "manual_outreach_asset_follow_through",
}

ACTIVE_REPAIR_WINDOW_STATUSES = {
    "needs_repair",
    "measurement_pending",
}

MEASUREMENT_HOLD_ACTION_TYPE = "measurement_hold_execution"
MEASUREMENT_HOLD_COOLDOWN_MINUTES = 60
LIVE_EXTERNAL_STATUSES = {
    "executed",
    "sent",
    "submitted",
    "published",
    "launched",
}

DISTRIBUTION_ARCHITECTURE_GUARD_REUSE_ACTION_TYPES = {
    "distribution_architecture_guard_follow_through",
    "distribution_architecture_guard_pause",
}

DISTRIBUTION_ARCHITECTURE_REUSE_LANES = {
    "distribution_architecture_repair",
    *DISTRIBUTION_ARCHITECTURE_GUARD_REUSE_ACTION_TYPES,
}

DISTRIBUTION_ARCHITECTURE_REUSE_ACTION_TYPE_MAP = {
    "distribution_architecture_repair": {
        "distribution_architecture_repair",
        "distribution_architecture_churn_guard_repair",
    },
    "distribution_architecture_guard_follow_through": {"distribution_architecture_guard_follow_through"},
    "distribution_architecture_guard_pause": {"distribution_architecture_guard_pause"},
}

POST_HOLD_RELEASE_SYNC_LANES = {
    'measurement_hold',
    'distribution_architecture_repair',
    'distribution_architecture_guard_follow_through',
    'distribution_architecture_guard_pause',
}


def _write_distribution_execution_log(
    *,
    distribution_lane: Any,
    execution: Any,
    now: datetime,
    reused_from_log: str | None = None,
) -> Path:
    """Write a first-class per-lane execution log so audits don't have to infer runtime state
    from the daily bundle only.
    """
    action_type_value = str(getattr(execution, 'action_type', '') or 'distribution_execution')
    safe_action = re.sub(r'[^a-z0-9_]+', '_', action_type_value.lower()).strip('_') or 'distribution_execution'
    path = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_{safe_action}.json"
    payload = {
        'timestamp': now.isoformat(),
        'run_type': 'marketing-distribution-execution',
        'chosen_action': {
            'type': getattr(execution, 'action_type', ''),
            'channel': getattr(distribution_lane, 'lane', ''),
            'title': f"Distribution lane execution: {getattr(distribution_lane, 'lane', '')}",
            'draft': getattr(execution, 'artifact_path', None),
        },
        'why_this_action': {
            'summary': getattr(distribution_lane, 'reason', ''),
            'supporting_reasons': list(getattr(distribution_lane, 'reasons', []) or []),
            'shared_findings_used': list(getattr(execution, 'shared_findings_used', []) or []),
            'targets_prepared': list(getattr(execution, 'targets_prepared', []) or []),
        },
        'result': {
            'status': getattr(execution, 'status', ''),
            'ok': getattr(execution, 'status', '') in LIVE_EXTERNAL_STATUSES or getattr(execution, 'status', '') in {'prepared', 'executed', 'skipped_repair'},
            'summary': getattr(execution, 'summary', ''),
            'targets_prepared': list(getattr(execution, 'targets_prepared', []) or []),
            'live_external_action': bool(getattr(execution, 'live_external_action', False)),
            'blocking_factors': list(getattr(execution, 'blocking_factors', []) or []),
        },
    }
    verification: dict[str, Any] = {}
    if reused_from_log:
        verification['reused_from_log'] = reused_from_log
        payload['result']['reused_existing_artifact'] = True
    if getattr(distribution_lane, 'lane', '') in DISTRIBUTION_ARCHITECTURE_REUSE_LANES:
        verification['execution_board_fingerprint'] = distribution_lane_selector._execution_board_fingerprint()
        verification['guard_reason'] = getattr(distribution_lane, 'reason', '')
    if verification:
        payload['verification'] = verification
    short_review_window_release_at = str(getattr(distribution_lane, 'short_review_window_release_at', '') or '').strip()
    if short_review_window_release_at and getattr(execution, 'action_type', '') in {'measurement_hold_execution', 'measurement_hold_follow_through'}:
        payload['review_window'] = {
            'scheduled_run_at': short_review_window_release_at,
        }
        why_this_action = payload.get('why_this_action') if isinstance(payload.get('why_this_action'), dict) else {}
        why_this_action['hold_until'] = short_review_window_release_at
        payload['why_this_action'] = why_this_action
    path.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    return path


def _repair_counts_as_live_outcome_repair(execution: Any) -> bool:
    return bool(getattr(execution, 'live_external_action', False)) or getattr(execution, 'action_type', '') in STRUCTURAL_REPLACEMENT_ACTION_TYPES


def _repair_counts_as_system_design_repair(execution: Any) -> bool:
    action_type = getattr(execution, 'action_type', '')
    return _repair_counts_as_live_outcome_repair(execution) or action_type in SYSTEM_DESIGN_REPAIR_ACTION_TYPES


def _advance_audit_repairs_for_execution(*, audit: dict[str, Any], execution: Any, now: datetime) -> bool:
    """Advance repair states only when this run actually honored or shipped them.

    Avoid fake-green transitions where needs_execution is flipped to pending_measurement
    before any qualifying replacement action has run.
    """
    changed = False
    lane_action = getattr(execution, 'action_type', '')
    shipped_live_repair = _repair_counts_as_live_outcome_repair(execution)
    shipped_system_design_repair = _repair_counts_as_system_design_repair(execution)
    repair_actions = audit.get('repair_actions', []) or []

    for repair in repair_actions:
        if repair.get('repair_state') != 'needs_execution':
            continue

        failure_type = repair.get('failure_type')
        should_advance = False

        if failure_type == 'primary_repo_flat':
            should_advance = shipped_live_repair
        elif failure_type == 'same_family_distribution_overlap':
            should_advance = lane_action not in DIRECTORY_LANE_ACTION_TYPES
        elif failure_type == 'same_family_outreach_overlap':
            should_advance = lane_action not in CURATOR_LANE_ACTION_TYPES or lane_action in MANUAL_CONTACT_REPAIR_ACTION_TYPES
        elif failure_type == 'same_family_publisher_overlap':
            should_advance = lane_action not in PUBLISHER_LANE_ACTION_TYPES
        elif repair.get('repair_kind') == 'system_design':
            should_advance = shipped_system_design_repair

        if should_advance:
            repair['repair_state'] = 'pending_measurement'
            repair['repair_acknowledged_at'] = now.isoformat()
            changed = True

    if changed:
        remaining_needs_execution = any(
            (repair or {}).get('repair_state') == 'needs_execution'
            for repair in repair_actions
        )
        if not remaining_needs_execution and audit.get('repair_window_status') == 'needs_repair':
            pending_reasons = audit.get('measurement_pending_reasons', []) or []
            audit['repair_window_status'] = 'measurement_pending' if pending_reasons else 'clear'

    return changed


def _load_active_pending_repairs(audit: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not audit:
        return []
    if audit.get('repair_window_status') not in ACTIVE_REPAIR_WINDOW_STATUSES:
        return []
    return [
        repair for repair in (audit.get('repair_actions', []) or [])
        if repair.get('repair_state') in {'needs_execution', 'pending_measurement'}
    ]


def _execution_board_surfaces_repo_proof_asset() -> bool:
    board_path = DRAFTS_DIR / 'marketing_execution_board_latest.md'
    try:
        text = board_path.read_text(encoding='utf-8').lower()
    except OSError:
        return False
    return 'repo conversion proof asset' in text and 'when: do now' in text


def _apply_repair_mode_overrides(
    distribution_lane: Any,
    pending_repairs: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> Any:
    if not pending_repairs:
        return distribution_lane

    skip_directory_submissions = any(
        repair.get('failure_type') == 'same_family_distribution_overlap'
        for repair in pending_repairs
    )
    skip_curator_outreach = any(
        repair.get('failure_type') == 'same_family_outreach_overlap'
        for repair in pending_repairs
    )
    primary_repo_flat_repair = next(
        (repair for repair in pending_repairs if repair.get('failure_type') == 'primary_repo_flat'),
        None,
    )
    object.__setattr__(distribution_lane, 'skip_directory_submissions', skip_directory_submissions)
    object.__setattr__(distribution_lane, 'skip_curator_outreach', skip_curator_outreach)

    current_lane = getattr(distribution_lane, 'lane', '')
    redirectable_lanes = {'directory_submission', 'curator_outreach', 'owned_content'}

    if primary_repo_flat_repair and current_lane in redirectable_lanes:
        redirect = (
            'directory_confirmation'
            if current_lane == 'directory_submission' and skip_directory_submissions
            else 'comparison_backlink_outreach'
        )
        if redirect == 'comparison_backlink_outreach' and distribution_lane_selector._comparison_backlink_lane_manual_only_blocked(
            now or datetime.now()
        ):
            if _execution_board_surfaces_repo_proof_asset():
                redirect = 'repo_conversion_proof_asset'
                reason_suffix = (
                    'the execution board already surfaces a repo-first proof asset, so ship that conversion asset '
                    'instead of collapsing back into a measurement hold.'
                )
            else:
                redirect = 'measurement_hold'
                reason_suffix = (
                    'the comparison/backlink queue is already fully prepared but GitHub auth is blocked here, '
                    'so hold for truthful follow-through instead of fabricating another comparison execution run.'
                )
        else:
            reason_suffix = (
                'refreshing live directory approval/backlink evidence instead of stacking more low-intent submissions.'
                if redirect == 'directory_confirmation' else
                'pushing Codeberg-primary comparison backlinks instead of saturated patterns.'
            )
        object.__setattr__(distribution_lane, 'lane', redirect)
        object.__setattr__(
            distribution_lane,
            'reason',
            'Repair override: primary_repo_flat repair active; ' + reason_suffix,
        )
        distribution_lane.reasons.insert(
            0,
            f"REPAIR: {primary_repo_flat_repair.get('action', primary_repo_flat_repair.get('failure_type', ''))[:120]}",
        )

    return distribution_lane


def _refresh_distribution_lane_after_execution(now: datetime, pending_repairs: list[dict[str, Any]] | None = None) -> Any:
    refreshed = choose_distribution_lane(
        now,
        write_action_log=False,
        persist_latest_artifacts=False,
    )
    if pending_repairs:
        refreshed = _apply_repair_mode_overrides(refreshed, pending_repairs, now=now)
    return refreshed


def _latest_lane_to_persist_after_execution(selected_lane: Any, refreshed_lane: Any, execution: Any) -> Any:
    selected_release = str(getattr(selected_lane, 'short_review_window_release_at', '') or '').strip()
    refreshed_release = str(getattr(refreshed_lane, 'short_review_window_release_at', '') or '').strip()
    selected_name = str(getattr(selected_lane, 'lane', '') or '').strip()
    refreshed_name = str(getattr(refreshed_lane, 'lane', '') or '').strip()
    execution_action_type = str(getattr(execution, 'action_type', '') or '').strip()

    if (
        selected_release
        and selected_name in DISTRIBUTION_ARCHITECTURE_REUSE_LANES
        and selected_name != 'distribution_architecture_guard_pause'
        and execution_action_type in DISTRIBUTION_ARCHITECTURE_REUSE_ACTION_TYPE_MAP.get(selected_name, set())
    ):
        if not refreshed_release and refreshed_name == 'distribution_architecture_guard_pause':
            return selected_lane
        if refreshed_name == 'owned_content':
            return selected_lane

    return refreshed_lane


def _sync_post_hold_release_run_if_needed(
    *,
    now: datetime,
    distribution_lane: Any,
    execution_board_path: Path,
    shared_findings_used: list[str],
) -> dict[str, Any]:
    lane_name = str(getattr(distribution_lane, 'lane', '') or '').strip()
    release_at = str(getattr(distribution_lane, 'short_review_window_release_at', '') or '').strip()
    if lane_name not in POST_HOLD_RELEASE_SYNC_LANES or not release_at:
        return {}

    reentry_contract_path = _write_post_hold_reentry_contract(
        now,
        release_at=release_at,
        execution_board_path=execution_board_path,
        shared_findings_used=shared_findings_used,
    )
    return _schedule_measurement_hold_release_run(
        now=now,
        release_at=release_at,
        shared_findings_used=shared_findings_used,
        reentry_contract_path=str(reentry_contract_path),
    )


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


def fetch_telegraph_views(url: str) -> int:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return 0
    slug = parsed.path.lstrip('/')
    if not slug:
        return 0
    api_url = f"https://api.telegra.ph/getViews/{slug}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if payload.get('ok') and isinstance(payload.get('result'), dict):
            return int(payload['result'].get('views') or 0)
    except Exception:
        return 0
    return 0


def fetch_post_views(post: dict) -> int:
    platform = str(post.get('platform') or '').strip().lower()
    url = str(post.get('url') or '').strip()
    if not url:
        return 0
    if platform == 'telegraph' or 'telegra.ph/' in url:
        return fetch_telegraph_views(url)
    return fetch_writeas_views(url)


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
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_primary_repo_flat_contact_discovery_refresh_log(
    *,
    now: datetime,
    discovery_generated_at: str | None,
    board_targets_before: list[str],
    board_targets_after: list[str],
    refresh_result: subprocess.CompletedProcess[str],
) -> Path:
    action_type = "primary_repo_flat_contact_discovery_staleness_repair"
    json_path = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_{action_type}.json"
    md_path = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_{action_type}.md"
    ok = refresh_result.returncode == 0
    summary = (
        "Refreshed stale primary-repo-flat contact discovery before lane selection so the current execution board reused a fresh publisher-contact artifact."
        if ok else
        "Tried to refresh stale primary-repo-flat contact discovery before lane selection, but the refresh failed."
    )
    payload = {
        "timestamp": now.isoformat(),
        "type": action_type,
        "status": "executed" if ok else "failed",
        "summary": summary,
        "why_this_action": {
            "findings": [
                "the execution board had no truthful do-now packet in the active review window",
                "the primary-repo-flat contact discovery artifact was stale enough to reduce the odds of the scheduled post-hold rerun finding a fresh publisher-contact lane",
            ],
            "shared_findings_used": [
                "marketing_execution_board_latest.md: no truthful do-now packet existed in the current review window",
                "primary_repo_flat_contact_discovery_latest.json: publisher-contact discovery is the canonical primary-repo-flat lane artifact",
                "market_intelligence_latest.json: contact discovery reuses competitor/comparison hooks",
                "adoption_metrics_latest.json: Codeberg movement remains the primary success gate",
            ],
            "stale_generated_at": discovery_generated_at,
            "board_targets_before": board_targets_before,
            "board_targets_after": board_targets_after,
        },
        "result": {
            "ok": ok,
            "status": "executed" if ok else "failed",
            "summary": summary,
            "stdout": (refresh_result.stdout or "").strip()[:1000],
            "stderr": (refresh_result.stderr or "").strip()[:1000],
            "board_target_count_before": len(board_targets_before),
            "board_target_count_after": len(board_targets_after),
            "live_external_action": False,
        },
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_lines = [
        "# Primary-repo-flat contact discovery staleness repair",
        f"Generated: {now.isoformat(timespec='seconds')}",
        "",
        "## Summary",
        summary,
        "",
        "## Why this ran",
        "- The execution board had no truthful do-now packet in the active review window.",
        f"- The latest publisher-contact discovery artifact timestamp was: {discovery_generated_at or 'missing'}.",
        "- Refreshing the shared publisher-contact artifact is a valid hold-window action because it improves the next rerun's odds without faking a fresh delivery lane.",
        "",
        "## Result",
        f"- Status: {'executed' if ok else 'failed'}",
        f"- Board targets before refresh: {len(board_targets_before)}",
        f"- Board targets after refresh: {len(board_targets_after)}",
    ]
    if refresh_result.stderr:
        md_lines.append(f"- stderr: {(refresh_result.stderr or '').strip()[:300]}")
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return json_path


def _refresh_primary_repo_flat_contact_discovery_for_empty_board(
    *,
    now: datetime,
    execution_board_path: Path,
    execution_board_targets: list[str],
) -> tuple[Path, list[str], Path | None]:
    if execution_board_targets:
        return execution_board_path, execution_board_targets, None

    discovery_payload = _load_json_file(PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_LATEST_PATH)
    generated_at = parse_iso_date(str(discovery_payload.get("generated_at") or "").strip())
    if generated_at is not None and now - generated_at < PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_STALE_AFTER:
        return execution_board_path, execution_board_targets, None

    refresh_result = subprocess.run(
        [sys.executable, str(PRIMARY_REPO_FLAT_CONTACT_DISCOVERY_SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=180,
    )

    refreshed_board_path = execution_board_path
    refreshed_board_targets = execution_board_targets
    if refresh_result.returncode == 0:
        refreshed_board_path, refreshed_board_targets = _write_marketing_execution_board(now)

    log_path = _write_primary_repo_flat_contact_discovery_refresh_log(
        now=now,
        discovery_generated_at=str(discovery_payload.get("generated_at") or "").strip() or None,
        board_targets_before=list(execution_board_targets),
        board_targets_after=list(refreshed_board_targets),
        refresh_result=refresh_result,
    )
    return refreshed_board_path, refreshed_board_targets, log_path


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
        item["views"] = fetch_post_views(post)
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
            "repair": "Replace stale owned-content-first repair. Hold homepage/owned-content volume at maintenance, push Codeberg-first conversion assets and third-party proof, and use only posting channels with current runtime proof instead of assuming a single default publication surface.",
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
            "action": "Continue only the owned/distribution channels that have current runtime proof, and keep Codeberg as the primary CTA.",
            "reason": "When adoption is moving, scale the channels with live execution proof instead of relying on stale channel doctrine.",
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


def _recent_marketing_log_payloads() -> list[tuple[Path, dict[str, Any], datetime]]:
    payloads: list[tuple[Path, dict[str, Any], datetime]] = []
    for path in LOG_DIR.glob("marketing_*.json"):
        if any(token in path.name for token in ("latest", "workflow_audit", "loop_runner", "loop_verifier", "independent_verification", "momentum_watchdog", "positioning_audit")):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        timestamp = parse_iso_date(payload.get("timestamp") or payload.get("timestamp_utc"))
        if timestamp is not None and timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)
        if timestamp is None:
            timestamp = datetime.fromtimestamp(path.stat().st_mtime)
        payloads.append((path, payload, timestamp))
    payloads.sort(key=lambda item: item[2], reverse=True)
    return payloads


def _latest_measurement_hold_window(now: datetime) -> dict[str, Any] | None:
    return shared_latest_measurement_hold_window(now, LOG_DIR)


def _latest_measurement_hold_skip_log(hold_window: dict[str, Any]) -> Path | None:
    hold_started_at = hold_window.get("hold_started_at")
    hold_until = hold_window.get("hold_until")
    source_log = str(hold_window.get("source_log") or "").strip()
    if not isinstance(hold_started_at, datetime) or not isinstance(hold_until, datetime) or not source_log:
        return None

    expected_hold_until = hold_until.isoformat()
    for path, payload, timestamp in _recent_marketing_log_payloads():
        if timestamp <= hold_started_at:
            continue
        chosen_action = payload.get("chosen_action") if isinstance(payload.get("chosen_action"), dict) else {}
        action_type = str(chosen_action.get("type") or payload.get("action_type") or "").strip()
        if action_type != "measurement_hold_cooldown_skip":
            continue
        why_this_action = payload.get("why_this_action") if isinstance(payload.get("why_this_action"), dict) else {}
        logged_source_log = str(why_this_action.get("source_log") or "").strip()
        logged_hold_until = str(why_this_action.get("hold_until") or "").strip()
        if logged_source_log == source_log and logged_hold_until == expected_hold_until:
            return path
    return None



def _write_measurement_hold_skip_log(now: datetime, hold_window: dict[str, Any]) -> Path:
    recent_skip_log = _latest_measurement_hold_skip_log(hold_window)
    if recent_skip_log is not None and recent_skip_log.exists():
        return recent_skip_log

    payload = {
        "timestamp": now.isoformat(),
        "run_type": "marketing-measurement-hold-skip",
        "chosen_action": {
            "type": "measurement_hold_cooldown_skip",
            "channel": "measurement_hold",
            "title": "Respect active measurement-hold cooldown",
        },
        "why_this_action": {
            "summary": "A recent measurement hold is still active, so this run should not burn more cycles on the same short-window evaluation churn.",
            "hold_started_at": hold_window["hold_started_at"].isoformat(),
            "hold_until": hold_window["hold_until"].isoformat(),
            "source_log": hold_window["source_log"],
        },
        "result": {
            "status": "skipped",
            "ok": True,
            "live_external_action": False,
        },
    }
    path = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d_%H%M%S')}_measurement_hold_skip.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _latest_measurement_hold_follow_through(hold_window: dict[str, Any]) -> dict[str, Any] | None:
    hold_started_at = hold_window.get("hold_started_at")
    if not isinstance(hold_started_at, datetime):
        return None

    for path, payload, timestamp in _recent_marketing_log_payloads():
        if timestamp <= hold_started_at:
            continue
        chosen_action = payload.get("chosen_action") if isinstance(payload.get("chosen_action"), dict) else {}
        action_type = str(chosen_action.get("type") or payload.get("action_type") or "").strip()
        if action_type != "measurement_hold_follow_through":
            continue
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        return {
            "timestamp": timestamp,
            "log_path": str(path),
            "artifact_path": chosen_action.get("draft") or payload.get("artifact_path") or "",
            "status": str(result.get("status") or "executed"),
            "summary": str(result.get("summary") or "Reused existing measurement-hold follow-through artifact."),
            "targets_prepared": list(result.get("targets_prepared") or []),
            "live_external_action": bool(result.get("live_external_action", False)),
            "blocking_factors": list(result.get("blocking_factors") or []),
        }
    return None


def _measurement_hold_truth_artifact_paths() -> list[Path]:
    return [
        LOG_DIR / "distribution_lane_latest.json",
        LOG_DIR / "primary_repo_flat_contact_discovery_latest.json",
        LOG_DIR / "curator_outreach_queue_latest.json",
        LOG_DIR / "comparison_backlink_queue_latest.json",
        LOG_DIR / "curator_contact_discovery_latest.json",
        LOG_DIR / "stackoverflow_answer_lane_latest.json",
        LOG_DIR / "apollo_sequence_status_latest.json",
        DRAFTS_DIR / "primary_repo_flat_contact_handoff_packet_latest.md",
        DRAFTS_DIR / "curator_handoff_packet_latest.md",
        DRAFTS_DIR / "curator_contact_handoff_packet_latest.md",
        DRAFTS_DIR / "comparison_backlink_handoff_packet_latest.md",
        DRAFTS_DIR / "stackoverflow_answer_handoff_packet_latest.md",
    ]


def _measurement_hold_follow_through_is_stale(recent_follow_through: dict[str, Any] | None) -> bool:
    if not recent_follow_through:
        return False
    follow_through_timestamp = recent_follow_through.get("timestamp")
    if not isinstance(follow_through_timestamp, datetime):
        return True

    for path in _measurement_hold_truth_artifact_paths():
        try:
            if path.exists() and datetime.fromtimestamp(path.stat().st_mtime) > follow_through_timestamp:
                return True
        except OSError:
            continue
    return False


def _distribution_architecture_execution_from_payload(
    *,
    path: Path,
    payload: dict[str, Any],
    timestamp: datetime,
    lane: str,
    action_types: set[str],
    current_fingerprint: str,
    expected_reason: str = '',
) -> dict[str, Any] | None:
    chosen_action = payload.get("chosen_action") if isinstance(payload.get("chosen_action"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}

    logged_lane = str(payload.get("selected_lane") or payload.get("lane") or "").strip()
    logged_action_type = str(
        chosen_action.get("type")
        or execution.get("action_type")
        or payload.get("selected_action_type")
        or payload.get("action_type")
        or ""
    ).strip()
    if logged_action_type not in action_types:
        return None
    if logged_lane and logged_lane != lane:
        return None

    verification = payload.get("verification") if isinstance(payload.get("verification"), dict) else {}
    logged_fingerprint = str(
        verification.get("execution_board_fingerprint")
        or payload.get("execution_board_fingerprint")
        or ""
    ).strip()
    if current_fingerprint:
        if logged_fingerprint:
            if logged_fingerprint != current_fingerprint:
                return None
        else:
            logged_reason = str((payload.get("why_this_action") or {}).get("summary") or payload.get("summary") or "").strip()
            if expected_reason and logged_reason != expected_reason:
                return None

    artifact_path = str(
        chosen_action.get("draft")
        or execution.get("artifact_path")
        or payload.get("artifact_path")
        or ""
    ).strip()
    summary = str(
        result.get("summary")
        or execution.get("summary")
        or payload.get("summary")
        or "Reused existing distribution-architecture guard artifact."
    )
    targets_prepared = result.get("targets_prepared") or execution.get("targets_prepared") or []
    live_external_action = result.get("live_external_action")
    if live_external_action is None:
        live_external_action = execution.get("live_external_action", False)
    blocking_factors = result.get("blocking_factors") or execution.get("blocking_factors") or []
    shared_findings_used = list((payload.get("why_this_action") or {}).get("shared_findings_used") or payload.get("shared_findings_used") or [])
    status = str(result.get("status") or execution.get("status") or payload.get("status") or "executed")

    return {
        "timestamp": timestamp,
        "log_path": str(path),
        "action_type": logged_action_type,
        "artifact_path": artifact_path,
        "status": status,
        "summary": summary,
        "targets_prepared": list(targets_prepared),
        "shared_findings_used": shared_findings_used,
        "live_external_action": bool(live_external_action),
        "blocking_factors": list(blocking_factors),
    }


def _latest_distribution_architecture_execution(lane: str, expected_reason: str = '') -> dict[str, Any] | None:
    action_types = DISTRIBUTION_ARCHITECTURE_REUSE_ACTION_TYPE_MAP.get(lane)
    if not action_types:
        return None

    current_fingerprint = distribution_lane_selector._execution_board_fingerprint()
    latest_match: dict[str, Any] | None = None
    for path, payload, timestamp in _recent_marketing_log_payloads():
        candidate = _distribution_architecture_execution_from_payload(
            path=path,
            payload=payload,
            timestamp=timestamp,
            lane=lane,
            action_types=action_types,
            current_fingerprint=current_fingerprint,
            expected_reason=expected_reason,
        )
        if candidate is None:
            continue
        latest_match = candidate
        break

    outcome_path = outcome_execution_board_runner.STATUS_JSON
    if outcome_path.exists():
        outcome_payload = _load_json_file(outcome_path)
        outcome_timestamp = parse_iso_date(str(outcome_payload.get("timestamp") or ""))
        if outcome_timestamp is not None:
            outcome_candidate = _distribution_architecture_execution_from_payload(
                path=outcome_path,
                payload=outcome_payload,
                timestamp=outcome_timestamp,
                lane=lane,
                action_types=action_types,
                current_fingerprint=current_fingerprint,
                expected_reason=expected_reason,
            )
            if outcome_candidate is not None and (
                latest_match is None or outcome_candidate["timestamp"] >= latest_match["timestamp"]
            ):
                latest_match = outcome_candidate

    return latest_match


def _latest_distribution_architecture_guard_execution(lane: str, expected_reason: str = '') -> dict[str, Any] | None:
    return _latest_distribution_architecture_execution(lane, expected_reason)


def _distribution_architecture_guard_execution_is_stale(
    recent_execution: dict[str, Any] | None,
    *,
    lane: str = '',
    now: datetime | None = None,
    short_review_window_release_at: str | None = None,
) -> bool:
    if not recent_execution:
        return False
    artifact_path = str(recent_execution.get("artifact_path") or "").strip()
    if artifact_path and not Path(artifact_path).exists():
        return True
    log_path = str(recent_execution.get("log_path") or "").strip()
    if log_path and not Path(log_path).exists():
        return True
    if lane in {"distribution_architecture_guard_follow_through", "distribution_architecture_guard_pause"} and now is not None:
        release_at = parse_iso_date(str(short_review_window_release_at or ""))
        short_window_started_at = (
            release_at - timedelta(hours=distribution_lane_selector.SHORT_REVIEW_WINDOW_HOURS)
            if release_at is not None
            else now - timedelta(hours=distribution_lane_selector.SHORT_REVIEW_WINDOW_HOURS)
        )
        execution_timestamp = recent_execution.get("timestamp")
        if not isinstance(execution_timestamp, datetime) or execution_timestamp < short_window_started_at:
            return True
    return False


def main() -> int:
    now = datetime.now()
    weekday = now.weekday()
    is_monday = weekday == 0

    hold_window = _latest_measurement_hold_window(now)
    if hold_window is not None:
        skip_log = _write_measurement_hold_skip_log(now, hold_window)

        # Measurement hold should pause external pushes, not internal learning.
        seo_current = run_seo_daily()
        seo_history = load_seo_trends(days=14)
        seo_trends = compute_trends(seo_current, seo_history)
        seo_insights_path = write_seo_insights(seo_current, [])

        AUDIT_PATH = LOG_DIR / "marketing_workflow_audit_latest.json"
        audit: dict[str, Any] = {}
        pending_repairs: list[dict[str, Any]] = []
        if AUDIT_PATH.exists():
            try:
                audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
                pending_repairs = _load_active_pending_repairs(audit)
            except (json.JSONDecodeError, OSError):
                audit = {}

        execution_board_path, execution_board_targets = _write_marketing_execution_board(now)
        execution_board_path, execution_board_targets, discovery_refresh_log = _refresh_primary_repo_flat_contact_discovery_for_empty_board(
            now=now,
            execution_board_path=execution_board_path,
            execution_board_targets=execution_board_targets,
        )
        if discovery_refresh_log is not None:
            print(f"[run.py] Refreshed stale primary-repo-flat contact discovery: {discovery_refresh_log}", flush=True)
        distribution_lane = choose_distribution_lane(now, persist_latest_artifacts=False)
        if pending_repairs:
            distribution_lane = _apply_repair_mode_overrides(distribution_lane, pending_repairs, now=now)
        _sync_post_hold_release_run_if_needed(
            now=now,
            distribution_lane=distribution_lane,
            execution_board_path=execution_board_path,
            shared_findings_used=list(getattr(distribution_lane, 'shared_findings_used', []) or []),
        )

        recent_follow_through = _latest_measurement_hold_follow_through(hold_window)
        reused_existing_follow_through = (
            distribution_lane.lane == "measurement_hold"
            and recent_follow_through is not None
            and not _measurement_hold_follow_through_is_stale(recent_follow_through)
        )

        if reused_existing_follow_through:
            lane_name = distribution_lane.lane
            lane_reason = distribution_lane.reason
            lane_artifact_path = getattr(distribution_lane, "artifact_path", "")
            execution_action_type = "measurement_hold_follow_through"
            execution_status = recent_follow_through.get("status", "executed")
            execution_artifact_path = recent_follow_through.get("artifact_path", "")
            execution_summary = recent_follow_through.get("summary", "Reused existing measurement-hold follow-through artifact.")
            execution_targets_prepared = list(recent_follow_through.get("targets_prepared") or [])
            execution_live_external_action = bool(recent_follow_through.get("live_external_action", False))
            execution_blocking_factors = list(recent_follow_through.get("blocking_factors") or [])
            distribution_execution = SimpleNamespace(
                lane=distribution_lane.lane,
                action_type=execution_action_type,
                status=execution_status,
                artifact_path=execution_artifact_path,
                summary=execution_summary,
                targets_prepared=execution_targets_prepared,
                shared_findings_used=[],
                live_external_action=execution_live_external_action,
                blocking_factors=execution_blocking_factors,
            )
            distribution_execution_log = Path(str(recent_follow_through["log_path"]))
        else:
            reused_existing_distribution_execution = False
            recent_guard_execution = None
            if distribution_lane.lane in DISTRIBUTION_ARCHITECTURE_REUSE_LANES:
                recent_guard_execution = _latest_distribution_architecture_guard_execution(
                    distribution_lane.lane,
                    expected_reason=distribution_lane.reason,
                )
                reused_existing_distribution_execution = (
                    recent_guard_execution is not None
                    and not _distribution_architecture_guard_execution_is_stale(
                        recent_guard_execution,
                        lane=distribution_lane.lane,
                        now=now,
                        short_review_window_release_at=getattr(distribution_lane, "short_review_window_release_at", None),
                    )
                )

            if reused_existing_distribution_execution and recent_guard_execution is not None:
                distribution_execution = SimpleNamespace(
                    lane=distribution_lane.lane,
                    action_type=recent_guard_execution.get("action_type", distribution_lane.lane),
                    status=recent_guard_execution.get("status", "executed"),
                    artifact_path=recent_guard_execution.get("artifact_path", ""),
                    summary=recent_guard_execution.get("summary", "Reused existing distribution-architecture guard artifact."),
                    targets_prepared=list(recent_guard_execution.get("targets_prepared") or []),
                    shared_findings_used=list(recent_guard_execution.get("shared_findings_used") or []),
                    live_external_action=bool(recent_guard_execution.get("live_external_action", False)),
                    blocking_factors=list(recent_guard_execution.get("blocking_factors") or []),
                )
                distribution_execution_log = Path(str(recent_guard_execution["log_path"]))
            else:
                distribution_execution = execute_distribution_lane(distribution_lane, now)
                distribution_execution_log = _write_distribution_execution_log(
                    distribution_lane=distribution_lane,
                    execution=distribution_execution,
                    now=now,
                )
            refreshed_lane = _refresh_distribution_lane_after_execution(now, pending_repairs)
            lane_name = distribution_lane.lane
            lane_reason = distribution_lane.reason
            lane_artifact_path = getattr(distribution_lane, "artifact_path", "")
            execution_action_type = distribution_execution.action_type
            execution_status = distribution_execution.status
            execution_artifact_path = distribution_execution.artifact_path
            execution_summary = distribution_execution.summary
            execution_targets_prepared = list(distribution_execution.targets_prepared or [])
            execution_live_external_action = bool(distribution_execution.live_external_action)
            execution_blocking_factors = list(distribution_execution.blocking_factors or [])

        latest_distribution_lane = distribution_lane if reused_existing_follow_through else _latest_lane_to_persist_after_execution(
            distribution_lane,
            refreshed_lane,
            distribution_execution,
        )
        distribution_lane_selector.persist_latest_lane_decision(
            latest_distribution_lane,
            now,
            write_action_log=False,
        )
        if not getattr(distribution_execution, 'lane', ''):
            distribution_execution.lane = distribution_lane.lane
        outcome_execution_board_runner.sync_from_execution(
            now=now,
            audit=audit,
            decision=distribution_lane,
            board_path=execution_board_path,
            board_targets=execution_board_targets,
            execution=distribution_execution,
        )

        payload = {
            "timestamp": now.isoformat(),
            "weekly_mode": is_monday,
            "marketing_status": "measurement_hold",
            "measurement_hold": {
                "active": True,
                "hold_started_at": hold_window["hold_started_at"].isoformat(),
                "hold_until": hold_window["hold_until"].isoformat(),
                "source_log": hold_window["source_log"],
                "reason": hold_window["reason"],
            },
            "distribution_lane": {
                "lane": lane_name,
                "reason": lane_reason,
                "artifact_path": lane_artifact_path,
            },
            "distribution_execution": {
                "action_type": execution_action_type,
                "status": execution_status,
                "artifact_path": execution_artifact_path,
                "summary": execution_summary,
                "targets_prepared": execution_targets_prepared,
                "live_external_action": execution_live_external_action,
                "blocking_factors": execution_blocking_factors,
                "reused_existing_follow_through": reused_existing_follow_through,
            },
            "seo": seo_current,
            "seo_trends": seo_trends,
            "seo_insights_path": str(seo_insights_path),
            "content_generation": {
                "returncode": 0,
                "stdout": "skipped: active measurement-hold cooldown",
                "stderr": "",
            },
            "posting": {
                "returncode": 0,
                "stdout": "skipped: active measurement-hold cooldown",
                "stderr": "",
            },
            "skip_log": str(skip_log),
            "distribution_execution_log": str(distribution_execution_log),
        }
        if not reused_existing_follow_through:
            payload["post_execution_distribution_lane"] = {
                "lane": latest_distribution_lane.lane,
                "reason": latest_distribution_lane.reason,
                "artifact_path": getattr(latest_distribution_lane, "artifact_path", ""),
            }
        log_file = LOG_DIR / f"marketing_{now.strftime('%Y-%m-%d')}.json"
        log_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        if reused_existing_follow_through:
            print(f"[run.py] Active measurement hold until {hold_window['hold_until'].isoformat()} — reusing existing follow-through artifact.", flush=True)
        else:
            print(f"[run.py] Active measurement hold until {hold_window['hold_until'].isoformat()} — running lightweight follow-through only.", flush=True)
        print(f"[run.py] Chosen distribution lane during hold: {lane_name}", flush=True)
        print(f"[run.py] Distribution execution log: {distribution_execution_log}", flush=True)
        if execution_artifact_path:
            print(f"[run.py] Distribution execution artifact: {execution_artifact_path}", flush=True)
        print(json.dumps(payload, indent=2))
        return 0

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
    audit: dict[str, Any] = {}
    pending_repairs: list[dict[str, Any]] = []
    if AUDIT_PATH.exists():
        try:
            audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            pending_repairs = _load_active_pending_repairs(audit)
        except (json.JSONDecodeError, OSError):
            audit = {}

    is_repair_mode = bool(pending_repairs)
    if is_repair_mode:
        skip_directory_submissions = any(
            repair.get("failure_type") == "same_family_distribution_overlap"
            for repair in pending_repairs
        )
        skip_curator_outreach = any(
            repair.get("failure_type") == "same_family_outreach_overlap"
            for repair in pending_repairs
        )
        print(
            f"[run.py] REPAIR MODE active — {len(pending_repairs)} active repairs, "
            f"skip_dir={skip_directory_submissions}, skip_curator={skip_curator_outreach}, "
            f"status={audit.get('repair_window_status', 'unknown')}",
            flush=True,
        )

    execution_board_path, execution_board_targets = _write_marketing_execution_board(now)
    execution_board_path, execution_board_targets, discovery_refresh_log = _refresh_primary_repo_flat_contact_discovery_for_empty_board(
        now=now,
        execution_board_path=execution_board_path,
        execution_board_targets=execution_board_targets,
    )
    if discovery_refresh_log is not None:
        print(f"[run.py] Refreshed stale primary-repo-flat contact discovery: {discovery_refresh_log}", flush=True)
    distribution_lane = choose_distribution_lane(now, persist_latest_artifacts=False)
    if is_repair_mode:
        original_lane = distribution_lane.lane
        distribution_lane = _apply_repair_mode_overrides(distribution_lane, pending_repairs, now=now)
        if distribution_lane.lane != original_lane:
            print(
                f"[run.py] repair override active — redirecting from {original_lane} to {distribution_lane.lane}",
                flush=True,
            )
    post_hold_release_schedule = _sync_post_hold_release_run_if_needed(
        now=now,
        distribution_lane=distribution_lane,
        execution_board_path=execution_board_path,
        shared_findings_used=list(getattr(distribution_lane, 'shared_findings_used', []) or []),
    )
    if post_hold_release_schedule.get('status') in {'scheduled', 'already_scheduled'}:
        print(
            f"[run.py] post-hold release wake synced for {post_hold_release_schedule.get('scheduled_run_at')}",
            flush=True,
        )

    reused_existing_distribution_execution = False
    recent_guard_execution = None
    if distribution_lane.lane in DISTRIBUTION_ARCHITECTURE_REUSE_LANES:
        recent_guard_execution = _latest_distribution_architecture_execution(
            distribution_lane.lane,
            expected_reason=distribution_lane.reason,
        )
        reused_existing_distribution_execution = (
            recent_guard_execution is not None
            and not _distribution_architecture_guard_execution_is_stale(
                recent_guard_execution,
                lane=distribution_lane.lane,
                now=now,
                short_review_window_release_at=getattr(distribution_lane, "short_review_window_release_at", None),
            )
        )

    if reused_existing_distribution_execution and recent_guard_execution is not None:
        distribution_execution = SimpleNamespace(
            lane=distribution_lane.lane,
            action_type=recent_guard_execution.get("action_type", distribution_lane.lane),
            status=recent_guard_execution.get("status", "executed"),
            artifact_path=recent_guard_execution.get("artifact_path", ""),
            summary=recent_guard_execution.get("summary", "Reused existing distribution-architecture guard artifact."),
            targets_prepared=list(recent_guard_execution.get("targets_prepared") or []),
            shared_findings_used=list(recent_guard_execution.get("shared_findings_used") or []),
            live_external_action=bool(recent_guard_execution.get("live_external_action", False)),
            blocking_factors=list(recent_guard_execution.get("blocking_factors") or []),
        )
        distribution_execution_log = Path(str(recent_guard_execution["log_path"]))
        refreshed_distribution_lane = distribution_lane
    else:
        distribution_execution = execute_distribution_lane(distribution_lane, now)
        distribution_execution_log = _write_distribution_execution_log(
            distribution_lane=distribution_lane,
            execution=distribution_execution,
            now=now,
        )
        refreshed_distribution_lane = _refresh_distribution_lane_after_execution(now, pending_repairs)
    latest_distribution_lane = _latest_lane_to_persist_after_execution(
        distribution_lane,
        refreshed_distribution_lane,
        distribution_execution,
    )
    distribution_lane_selector.persist_latest_lane_decision(
        latest_distribution_lane,
        now,
        write_action_log=False,
    )
    outcome_execution_board_runner.sync_from_execution(
        now=now,
        audit=audit,
        decision=distribution_lane,
        board_path=execution_board_path,
        board_targets=execution_board_targets,
        execution=distribution_execution,
    )
    print(f"[run.py] Chosen distribution lane: {distribution_lane.lane}", flush=True)
    print(f"[run.py] Distribution execution log: {distribution_execution_log}", flush=True)
    if distribution_execution.artifact_path:
        print(f"[run.py] Distribution execution artifact: {distribution_execution.artifact_path}", flush=True)

    if is_repair_mode and AUDIT_PATH.exists():
        try:
            audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            if _advance_audit_repairs_for_execution(audit=audit, execution=distribution_execution, now=now):
                AUDIT_PATH.write_text(json.dumps(audit, indent=2), encoding="utf-8")
                print("[run.py] repair_state advanced after qualifying execution", flush=True)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[run.py] WARNING: could not advance audit repair state: {e}", flush=True)

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
        "distribution_execution_log": str(distribution_execution_log),
        "post_hold_release_schedule": post_hold_release_schedule,
        "post_execution_distribution_lane": latest_distribution_lane.__dict__,
        "reused_existing_distribution_execution": reused_existing_distribution_execution,
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
