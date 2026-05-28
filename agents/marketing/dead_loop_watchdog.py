#!/usr/bin/env python3
"""Dead-loop watchdog for marketing cron jobs.

Detects when the marketing loop is stuck in distribution_architecture_repair
cycles without a live external action between them. If >= 3 consecutive
repairs happen with the same execution-board fingerprint and no live action,
the watchdog escalates by modifying the active-lane state to force a
different lane.

Rule: On 3rd+ strike, inject a 'dead_loop_escalation' lane that binds the
next cron slot to create a new distribution asset or process change
instead of another measurement hold.
"""

from __future__ import annotations

import json, os, sys, hashlib
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get(
    "OPENCLAW_WORKSPACE",
    os.path.expanduser("~/.openclaw/workspace"),
))

LOG_DIR = WORKSPACE / "agents" / "marketing" / "logs"
LANE_FILE = LOG_DIR / "distribution_lane_latest.json"
ESCALATION_FILE = WORKSPACE / "drafts" / "dead_loop_escalation.json"
RUN_LOG = WORKSPACE / "agents" / "marketing" / "logs" / "dead_loop_watchdog.json"

# Lane types that count as "real work" (break the dead-loop chain)
LIVE_ACTIONS = {
    "reddit_post", "reddit_comment", "hn_post", "curator_outreach",
    "publisher_outreach", "comparison_page", "dev_to_post",
    "directory_submission", "contact_form_submission",
    "apollo_sequence", "product_hunt_submission",
    "stackoverflow_answer", "stackoverflow_question",
    "github_discussion_post", "content_publish",
    "repo_conversion_proof", "seo_page_published",
}

# Lane types that count as dead-loop filler
DEAD_LOOP_LANES = {
    "measurement_hold",
    "distribution_architecture_repair",
    "distribution_architecture_guard_pause",
    "distribution_architecture_guard_pause_execution",
}


def get_fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def load_recent_actions(limit: int = 30) -> list[dict]:
    """Load recent marketing action logs, newest first."""
    actions = []
    if not LOG_DIR.exists():
        return actions

    for f in sorted(LOG_DIR.glob("marketing_*.json"), reverse=True):
        if f.name.startswith("marketing_20"):
            try:
                data = json.loads(f.read_text())
                if isinstance(data, dict) and "chosen_action" in data:
                    actions.append(data)
            except (json.JSONDecodeError, KeyError):
                continue
            if len(actions) >= limit:
                break

    return actions


def extract_action_type(action: dict) -> str:
    """Extract the action type string from a log entry."""
    chosen = action.get("chosen_action", {})
    action_type = chosen.get("type", "")
    channel = chosen.get("channel", "")

    if not action_type and channel:
        action_type = channel

    return action_type or "unknown"


def extract_board_fingerprint(action: dict) -> str | None:
    """Extract board fingerprint from verification data."""
    verif = action.get("verification", {})
    fp = verif.get("execution_board_fingerprint")
    return fp


def is_live_action(action: dict) -> bool:
    """Determine if this action was a real external marketing action."""
    atype = extract_action_type(action)

    # Check if status says it was executed externally
    result = action.get("result", {})
    if result.get("status") in ("executed", "submitted", "published", "posted"):
        return True

    if atype in LIVE_ACTIONS:
        return True

    if atype in DEAD_LOOP_LANES:
        return False

    # Check for explicit outcome-ready flag
    verif = action.get("verification", {})
    if verif.get("outcome_ready"):
        return True

    # A live_external_action=true flag is a strong signal
    if action.get("live_external_action"):
        return True

    return False


def count_consecutive_dead_loops(actions: list[dict]) -> int:
    """Count how many consecutive dead-loop actions exist at the tail.
    
    Returns (consecutive_count, last_live_action_index, shared_fingerprint).
    shared_fingerprint is the fingerprint of the first dead loop in the chain
    if all dead loops share it, else None.
    """
    dead_count = 0
    last_live_idx = -1
    fingerprints: list[str] = []

    for i, action in enumerate(actions):
        if is_live_action(action):
            last_live_idx = i
            break
        atype = extract_action_type(action)
        if atype in DEAD_LOOP_LANES:
            dead_count += 1
            fp = extract_board_fingerprint(action)
            if fp:
                fingerprints.append(fp)
        # For unknown types, stop counting but don't treat as live
        # unless it has outcome_ready

    # Check if all counted dead loops share the same fingerprint
    shared_fp = None
    if fingerprints and len(fingerprints) == dead_count:
        # Count occurrences of the most common fingerprint
        from collections import Counter
        fp_counts = Counter(fingerprints)
        most_common_fp, count = fp_counts.most_common(1)[0]
        # If >= 3 entries share this fingerprint, it's a shared dead loop
        if count >= 3:
            shared_fp = most_common_fp

    return dead_count, last_live_idx, shared_fp


def run() -> dict:
    """Execute the watchdog check and return findings."""
    now = datetime.now(timezone.utc).isoformat()
    actions = load_recent_actions()
    dead_count, last_live_idx, shared_fp = count_consecutive_dead_loops(actions)

    result = {
        "timestamp": now,
        "total_actions_reviewed": len(actions),
        "consecutive_dead_loops": dead_count,
        "actions_since_last_live": last_live_idx if last_live_idx >= 0 else "never",
        "shared_board_fingerprint": shared_fp,
        "escalation_triggered": False,
        "action_taken": None,
    }

    if dead_count >= 3:
        result["escalation_triggered"] = True

        # Write escalation file that the marketing loop can read
        escalation = {
            "timestamp": now,
            "consecutive_dead_loops": dead_count,
            "shared_board_fingerprint": shared_fp,
            "message": (
                f"DEAD LOOP DETECTED: {dead_count} consecutive dead-loop cycles "
                f"({','.join(extract_action_type(a) for a in actions[:dead_count])}) "
                f"without a live external action. "
                f"Escalation mandates a process/agent repair or new distribution asset "
                f"in the next slot. Do not emit another measurement_hold or "
                f"distribution_architecture_repair."
            ),
            "mandated_next_slot": "process_repair_or_new_asset",
            "blocked_lanes": list(DEAD_LOOP_LANES),
            "suggested_actions": [
                "Create a new distribution agent targeting an untouched channel",
                "Build a new SEO page on the Ralph Site",
                "Submit to Product Hunt or similar launch platform",
                "Write a Dev.to article series",
                "Create a YouTube walkthrough",
                "Patch the marketing cron job to detect this state itself",
            ],
        }
        result["action_taken"] = "escalation_file_written"
        ESCALATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        ESCALATION_FILE.write_text(json.dumps(escalation, indent=2) + "\n")

        # Also patch the distribution lane to force a different lane
        if LANE_FILE.exists():
            try:
                lane_data = json.loads(LANE_FILE.read_text())
                lane_data["lane"] = "dead_loop_escalation"
                lane_data["reason"] = (
                    f"Watchdog escalated after {dead_count} dead loops "
                    f"with fingerprint {shared_fp}"
                )
                lane_data["escalation_source"] = "dead_loop_watchdog"
                LANE_FILE.write_text(json.dumps(lane_data, indent=2) + "\n")
                result["action_taken"] += " + lane_patched"
            except Exception as e:
                result["patch_error"] = str(e)
    else:
        # Not escalated — clean up any stale escalation
        if ESCALATION_FILE.exists():
            ESCALATION_FILE.unlink()

    # Write self-log
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text(json.dumps(result, indent=2) + "\n")

    # Print summary
    if dead_count >= 3:
        print(f"🚨 DEAD LOOP: {dead_count} consecutive dead-loop actions detected")
        print(f"   Shared fingerprint: {shared_fp}")
        print(f"   Escalation written to {ESCALATION_FILE}")
        print(f"   Distribution lane patched to dead_loop_escalation")
    else:
        print(f"✅ Normal: {dead_count} dead loops (threshold: 3)")

    return result


if __name__ == "__main__":
    result = run()
    json.dump(result, sys.stdout, indent=2)
    print()
