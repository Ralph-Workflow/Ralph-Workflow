#!/usr/bin/env python3
"""
HN/Lobsters stalemate resolver.

Checks once daily whether the handoff packet is current and whether the target
surfaces are reachable. Produces a simple READY or BLOCKED output instead of
re-diagnosing the same bottleneck for the 9th time.

Usage: python3 hn_lobsters_preflight.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
PACKET = ROOT / "drafts/HN_LOBSTERS_ACTIVE_PACKET.md"
LOG = ROOT / "agents/marketing/logs/hn_lobsters_preflight_latest.json"
STALE_DAYS = 7


def packet_is_stale() -> bool:
    """Return True if the packet file is older than STALE_DAYS."""
    if not PACKET.exists():
        return True
    mtime = datetime.fromtimestamp(PACKET.stat().st_mtime, tz=timezone.utc)
    return datetime.now(timezone.utc) - mtime > timedelta(days=STALE_DAYS)


def packet_fingerprint() -> str | None:
    """Return a hash of the current packet content, or None if missing."""
    if not PACKET.exists():
        return None
    return hashlib.sha256(PACKET.read_bytes()).hexdigest()[:12]


def check_reachability() -> dict:
    """Probe HN and Lobsters for basic reachability (DNS + HTTP)."""
    import socket
    import urllib.request

    results: dict = {
        "hn": {"reachable": False, "reason": None},
        "lobsters": {"reachable": False, "reason": None},
    }

    for label, host in [("hn", "news.ycombinator.com"), ("lobsters", "lobste.rs")]:
        try:
            socket.gethostbyname(host)
            try:
                req = urllib.request.Request(f"https://{host}", headers={"User-Agent": "RalphWorkflow/1.0"})
                urllib.request.urlopen(req, timeout=10)
                results[label]["reachable"] = True
            except Exception as e:
                results[label]["reason"] = f"HTTP: {e}"
        except Exception as e:
            results[label]["reason"] = f"DNS: {e}"

    return results


def main() -> int:
    # ── Spidering guard: HN/Lobsters permanently blocked (9+ cycles stalemated) ──
    try:
        from agents.marketing.channel_spidering_guard import guard_check, guard_record
        for ch in ["hackernews", "lobsters"]:
            allowed, reason, remaining = guard_check(ch)
            if not allowed:
                result = {"timestamp": datetime.now(timezone.utc).isoformat(), "status": "spidering_blocked", "channel": ch, "reason": reason, "live_external_action": False}
                LOG.parent.mkdir(parents=True, exist_ok=True)
                LOG.write_text(json.dumps(result, indent=2))
                guard_record(ch, ok=False, fingerprint="spidering_guard_rejected")
                print(f"BLOCKED: {ch} — {reason}")
                return 1
    except ImportError:
        pass

    fp = packet_fingerprint()
    stale = packet_is_stale()
    reachability = check_reachability()

    any_reachable = reachability["hn"]["reachable"] or reachability["lobsters"]["reachable"]

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "packet_path": str(PACKET),
        "packet_fingerprint": fp,
        "packet_stale": stale,
        "reachability": reachability,
        "status": "READY" if (not stale and any_reachable) else "BLOCKED",
        "blocked_reasons": [],
    }

    if stale:
        result["blocked_reasons"].append("packet_stale")
    if fp is None:
        result["blocked_reasons"].append("packet_missing")

    if not any_reachable:
        if not reachability["hn"]["reachable"]:
            result["blocked_reasons"].append(f"hn_unreachable: {reachability['hn']['reason']}")
        if not reachability["lobsters"]["reachable"]:
            result["blocked_reasons"].append(f"lobsters_unreachable: {reachability['lobsters']['reason']}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "w") as f:
        json.dump(result, f, indent=2, default=str)

    if result["status"] == "READY":
        print(f"READY — packet fresh (fp={fp}), surfaces reachable")
        print(f"Copy {PACKET} and submit manually.")
        return 0
    else:
        print(f"BLOCKED — {', '.join(result['blocked_reasons'])}")
        print(f"Log: {LOG}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
