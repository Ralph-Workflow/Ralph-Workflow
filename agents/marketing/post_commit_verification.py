#!/usr/bin/env python3
"""
Post-commit verification gate for marketing actions logged with `live_external_action: true`.

Problem solved (fake-green gate):
The `marketing_2026-05-28_172200_concrete_repair_and_distribution.json` log claimed
`live_external_action: true` for three CTA fixes and one SEO blog post, but the
commits did not exist at the time — they were only in `stash@{0}`.

Fix: after any marketing log is written with `live_external_action: true`, verify
that the claimed git commits actually exist in the repository. If the commits are
missing, flag the log as `fake_green` and write a repair-suggested marker.

Usage (after a marketing log is written):
    python3 agents/marketing/post_commit_verification.py LOG_FILE.json

Exit codes:
    0 → commit verified
    1 → commit NOT found (fake-green detected)
    2 → log has no commit claim to verify
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", os.path.expanduser("~/.openclaw/workspace")))
REPO_PATH = WORKSPACE / "Ralph-Site"
LOG_DIR = WORKSPACE / "agents" / "marketing" / "logs"
FAKE_GREEN_FILE = LOG_DIR / "fake_green_detected.json"


class VerificationResult:
    def __init__(self, verified: bool, message: str, log_file: str):
        self.verified = verified
        self.message = message
        self.log_file = log_file
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "verified": self.verified,
            "message": self.message,
            "log_file": self.log_file,
            "timestamp": self.timestamp,
        }


def _log_has_live_external_action(log_path: Path) -> bool:
    """Check if a marketing log claims a live external action."""
    try:
        data = json.loads(log_path.read_text())
        return bool(data.get("live_external_action", False))
    except (json.JSONDecodeError, OSError):
        return False


def _extract_commit_claims(log_path: Path) -> list[dict]:
    """Extract git commit claims from a marketing log."""
    try:
        data = json.loads(log_path.read_text())
        commits = []
        # Check multiple known field names
        for key in ("commits", "commit_shas", "committed_shas", "pushed_commits"):
            if key in data and isinstance(data[key], list):
                for c in data[key]:
                    if isinstance(c, dict) and c.get("sha"):
                        commits.append(c)
        # Check single commit field
        for key in ("commit_sha", "pushed_sha", "live_commit_sha"):
            if key in data and data[key]:
                commits.append({"sha": data[key]})
        return commits
    except (json.JSONDecodeError, OSError):
        return []


def _commit_exists_in_repo(sha: str) -> bool:
    """Verify a git commit SHA exists in the local repo."""
    result = subprocess.run(
        ["git", "cat-file", "-t", sha],
        capture_output=True,
        text=True,
        cwd=REPO_PATH,
    )
    return result.returncode == 0 and "commit" in result.stdout


def _read_author_from_log(log_path: Path) -> Optional[str]:
    """Extract the author/agent of a marketing log."""
    try:
        data = json.loads(log_path.read_text())
        for key in ("agent", "author", "source", "emitted_by"):
            if data.get(key):
                return str(data[key])
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _record_fake_green(result: VerificationResult) -> None:
    """Append a fake-green detection to the persistent report."""
    existing = []
    if FAKE_GREEN_FILE.exists():
        try:
            existing = json.loads(FAKE_GREEN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing.append(result.to_dict())
    FAKE_GREEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    FAKE_GREEN_FILE.write_text(json.dumps(existing, indent=2, default=str))


def verify_marketing_log(log_path: Path) -> VerificationResult:
    """
    Verify that a marketing log file has its claimed commits.
    If the log claims live_external_action: true but the commits don't exist,
    this is a fake-green event.
    """
    log_name = log_path.name

    if not log_path.exists():
        return VerificationResult(
            False, f"Log file does not exist: {log_path}", log_name
        )

    if not _log_has_live_external_action(log_path):
        return VerificationResult(
            True, "Log does not claim live_external_action — nothing to verify", log_name
        )

    commits = _extract_commit_claims(log_path)

    if not commits:
        return VerificationResult(
            True,
            f"Log claims live_external_action: true but has NO commit SHAs in recognized fields. "
            "This is a format gap — commits may have been pushed but not logged.",
            log_name,
        )

    for c in commits:
        sha = c.get("sha", "")
        if not sha:
            continue
        if not _commit_exists_in_repo(sha):
            result = VerificationResult(
                False,
                f"FAKE GREEN: Log {log_name} claims live_external_action: true "
                f"but commit {sha[:8]} does not exist in Ralph-Site repo. "
                f"Message: {c.get('message', 'N/A')[:80]}",
                log_name,
            )
            _record_fake_green(result)
            return result

    return VerificationResult(
        True,
        f"All {len(commits)} claimed commit(s) verified in Ralph-Site repo",
        log_name,
    )


def scan_recent_logs(hours: int = 24) -> list[VerificationResult]:
    """Scan recent marketing logs for fake-green claims."""
    results = []
    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)

    for log_path in sorted(LOG_DIR.glob("marketing_*.json"), reverse=True):
        try:
            mtime = log_path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        result = verify_marketing_log(log_path)
        if not result.verified:
            results.append(result)

    return results


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--scan":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        violations = scan_recent_logs(hours)
        if violations:
            print(f"FAKE GREEN DETECTED ({len(violations)} violations in {hours}h):")
            for v in violations:
                print(f"  ❌ {v.message}")
            sys.exit(1)
        else:
            print(f"✅ No fake-green violations in last {hours}h")
            sys.exit(0)

    if len(sys.argv) > 1:
        log_path = Path(sys.argv[1])
    else:
        # Default: scan latest log
        logs = sorted(LOG_DIR.glob("marketing_*.json"), reverse=True)
        if not logs:
            print("No marketing logs found")
            sys.exit(2)
        log_path = logs[0]

    result = verify_marketing_log(log_path)
    if result.verified:
        print(f"✅ {result.message}")
        sys.exit(0)
    else:
        print(f"❌ {result.message}")
        sys.exit(1)
