#!/usr/bin/env python3
"""
Cross-channel spidering guard — prevents rapid-fire re-hits of
blocked, stale, or recently-tried channels.

Problem solved: 80+ log files/day from identical channel attempts
that have 0% chance of success (reCAPTCHA-blocked, IP-banned, etc).

Each channel gets:
- A minimum cooldown between attempts (configurable by channel status)
- Permanently-blocked channels get a stop-file that lives on disk
- Attempts within cooldown are rejected with channel_spidering_guard BLOCK
- A fingerprint of the last attempt lets the material-change-gate skip
  duplicate no-op actions

Usage:
    from agents.marketing.channel_spidering_guard import (
        guard_check, guard_record, guard_block, ChannelStatus
    )

    status, reason, cooldown_remaining = guard_check("dev.to")
    if not status:
        print(f"BLOCKED: {reason} ({cooldown_remaining:.1f}h remaining)")
        return

    # ... attempt channel action ...
    guard_record("dev.to", ok=result_ok)
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

ROOT = Path("/home/mistlight/.openclaw/workspace")

# Channels listed here are hard-perma-blocked (human-gated, auth-impossible).
# They differ from runtime-detected spidering which is handled by cooldown alone.
STATE_FILE = ROOT / "agents/marketing/logs/channel_spidering_state.json"
STOP_DIR = ROOT / "agents/marketing/logs/channel_blocked"

# ── Channel cooldown policy (hours) ──────────────────────────────────
# Channels should NEVER be hit more often than this unless a material
# change occurred (new content, new approach, new credentials, etc.)
DEFAULT_COOLDOWN_HOURS = {
    "dev.to": 24.0,              # reCAPTCHA-blocked, 0% success rate
    "reddit": 24.0,              # IP-blocked at Hetzner, Tor-blocked
    "reddit-watchdog": 12.0,      # monitoring-only, still blocked
    "hackernews": 24.0,           # human-gated, 8+ cycles stalemated
    "lobsters": 24.0,             # human-gated, invite-wall
    "apollo": 12.0,               # in measurement window, no changes
    "apollo-outreach": 24.0,      # spam-body emergency, don't touch
    "github-discussions": 24.0,    # once-daily draft banking, not a distribution lane
    "github-discussions-search": 24.0,
    "stackoverflow": 12.0,        # Q&A, low-urgency
    "pypi": 12.0,                 # README check only unless release
    "mastodon": 24.0,             # anti-bot active
    "smtp-outreach": 24.0,        # no SMTP_USER, can't send
    "primary_repo_flat_contact_discovery": 6.0,
    "telegraph": 0.25,             # blog cross-post — once-daily cron, 15min guard against double-fire
    "comparison_backlink": 24.0,    # permanently blocked — no gh auth, cannot submit PRs
}

# Channels that are PERMANENTLY BLOCKED and should never be attempted
# without human intervention. Stop files live in STOP_DIR/<channel>.txt
PERMANENTLY_BLOCKED: dict[str, str] = {
    "dev.to": "reCAPTCHA on signup — headless browser cannot solve. 6+ consecutive failures. Requires human CAPTCHA solve or different IP.",
    "reddit": "IP-blocked at Hetzner Helsinki. Tor also blocked. No proxy path available. ARCHITECTURALLY RETIRED 2026-05-28.",
    "smtp-outreach": "SMTP_USER environment variable not set. No send capability. Requires human credential handoff.",
    "hackernews": "Human-gated posting. Show HN packet created but unposted across 9+ audit cycles (structural ceiling rule triggered at 3). Packet generation must stop. Requires human to post.",
    "lobsters": "Human-gated invite-wall. 9+ cycles stalemated, no invitation obtained. Packet generation must stop. Requires human to obtain invite and post.",
    "comparison_backlink": "8 prepared comparison PRs, 0 delivery path. gh auth login missing — cannot submit PRs. Prepared-but-undeliverable across 3+ cycles. Must not be selected as a distribution lane until gh auth succeeds.",
}


class ChannelStatus(Enum):
    OK = "ok"
    COOLDOWN = "cooldown"
    PERMANENTLY_BLOCKED = "permanently_blocked"
    SPIDERING = "spidering"


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _check_stop_file(channel: str) -> Optional[str]:
    """Return reason if channel has permanent stop file, None otherwise."""
    stop_path = STOP_DIR / f"{channel}.txt"
    if stop_path.exists():
        content = stop_path.read_text().strip()
        if content:
            return content
        return "permanently_blocked"
    return None


def _write_stop_file(channel: str, reason: str) -> None:
    """Permanently block a channel with a human-readable stop file."""
    STOP_DIR.mkdir(parents=True, exist_ok=True)
    stop_path = STOP_DIR / f"{channel}.txt"
    stop_path.write_text(
        f"CHANNEL PERMANENTLY BLOCKED: {channel}\n"
        f"Reason: {reason}\n"
        f"Blocked at: {datetime.now(timezone.utc).isoformat()}\n"
        f"To unblock: delete this file AND fix the underlying issue.\n"
    )


def guard_check(channel: str) -> tuple[bool, str, float]:
    """
    Check whether a channel action should be allowed.

    Returns: (allowed, reason, cooldown_remaining_hours)
    """
    # 1. Permanent block check
    stop_reason = _check_stop_file(channel)
    if stop_reason:
        return False, f"permanently_blocked: {stop_reason}", float("inf")

    # 2. Programmatic permanent block
    if channel in PERMANENTLY_BLOCKED:
        reason = PERMANENTLY_BLOCKED[channel]
        # Auto-write stop file if it doesn't exist yet
        if not _check_stop_file(channel):
            _write_stop_file(channel, reason)
        return False, f"permanently_blocked: {reason}", float("inf")

    # 3. Cooldown check
    cooldown = DEFAULT_COOLDOWN_HOURS.get(channel, 4.0)
    state = _load_state()
    channel_state = state.get(channel, {})
    last_attempt_ts = channel_state.get("last_attempt_ts")

    if last_attempt_ts:
        last_attempt = datetime.fromisoformat(last_attempt_ts.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_attempt).total_seconds() / 3600

        if elapsed < cooldown:
            remaining = cooldown - elapsed
            return False, f"cooldown_active", remaining

    return True, "ok", 0.0


def guard_record(channel: str, ok: bool, fingerprint: str = "", **kwargs) -> None:
    """Record a channel attempt (successful or failed)."""
    state = _load_state()
    channel_state = state.get(channel, {})
    channel_state["last_attempt_ts"] = datetime.now(timezone.utc).isoformat()
    channel_state["last_ok"] = ok
    channel_state["total_attempts"] = channel_state.get("total_attempts", 0) + 1
    channel_state["total_failures"] = channel_state.get("total_failures", 0) + (0 if ok else 1)
    channel_state["last_fingerprint"] = fingerprint
    channel_state.update(kwargs)

    state[channel] = channel_state
    _save_state(state)

    # After 10 consecutive failures, permanently block
    if channel_state["total_failures"] >= 10 and channel_state["total_attempts"] >= 10:
        _write_stop_file(
            channel,
            f"{channel_state['total_failures']}/{channel_state['total_attempts']} failures. "
            f"Auto-perma-blocked by channel_spidering_guard."
        )


def guard_block(channel: str, reason: str = "") -> None:
    """Permanently block a channel with a reason."""
    _write_stop_file(channel, reason)


def guard_unblock(channel: str) -> bool:
    """Remove permanent block from a channel. Returns True if unblocked."""
    stop_path = STOP_DIR / f"{channel}.txt"
    if stop_path.exists():
        stop_path.unlink()
        return True
    return False


def guard_status(channel: str = "") -> dict:
    """Get status for a channel or all channels."""
    state = _load_state()
    if channel:
        result = state.get(channel, {})
        result["permanently_blocked"] = bool(_check_stop_file(channel))
        result["permanently_blocked_reason"] = PERMANENTLY_BLOCKED.get(channel, "")
        result["cooldown_hours"] = DEFAULT_COOLDOWN_HOURS.get(channel, 4.0)
        return result
    # All channels
    result = {}
    for ch in set(list(DEFAULT_COOLDOWN_HOURS.keys()) + list(state.keys())):
        info = state.get(ch, {})
        info["permanently_blocked"] = bool(_check_stop_file(ch))
        info["permanently_blocked_reason"] = PERMANENTLY_BLOCKED.get(ch, "")
        info["cooldown_hours"] = DEFAULT_COOLDOWN_HOURS.get(ch, 4.0)
        result[ch] = info
    return result


def guard_status_json() -> str:
    """JSON status dump for machine consumption."""
    return json.dumps(guard_status(), indent=2, default=str)


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--status":
            print(guard_status_json())
        elif cmd == "--check" and len(sys.argv) > 2:
            ok, reason, remaining = guard_check(sys.argv[2])
            print(json.dumps({"channel": sys.argv[2], "ok": ok, "reason": reason, "cooldown_remaining_h": remaining}))
        elif cmd == "--block" and len(sys.argv) > 2:
            reason = sys.argv[3] if len(sys.argv) > 3 else "manual block"
            guard_block(sys.argv[2], reason)
            print(f"BLOCKED {sys.argv[2]}: {reason}")
        elif cmd == "--unblock" and len(sys.argv) > 2:
            unblocked = guard_unblock(sys.argv[2])
            print(f"{'UNBLOCKED' if unblocked else 'NOT_FOUND'} {sys.argv[2]}")
        elif cmd == "--init":
            # Write permanent stop files for all programmatically blocked channels
            for ch, reason in PERMANENTLY_BLOCKED.items():
                if not _check_stop_file(ch):
                    _write_stop_file(ch, reason)
                    print(f"INIT-STOP {ch}: {reason}")
            print("channel_spidering_guard initialized")
        else:
            print("Usage: python3 channel_spidering_guard.py [--status|--check CHANNEL|--block CHANNEL REASON|--init]")
    else:
        print(guard_status_json())
