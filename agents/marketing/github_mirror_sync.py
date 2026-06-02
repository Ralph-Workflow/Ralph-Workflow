#!/usr/bin/env python3
"""GitHub Mirror Sync Agent — keeps the GitHub mirror in sync with Codeberg primary.

This is a direct architecture repair: the mirror was found 10 commits behind Codeberg on
2026-05-29 with no automation keeping it synced. A stale mirror means stale PyPI README
(the publish workflow runs from GitHub tags), so drift directly blocks conversion upgrades.

Syncs every 30 min. Logs to agents/marketing/logs/mirror_sync_cron.log.
"""

import subprocess
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

MIRROR_DIR = Path("/home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror")
LOG_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing/logs")
LOG_PATH = LOG_DIR / "mirror_sync_cron.log"

def log_entry(status: str, detail: str, **extra) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "detail": detail,
        **extra,
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def run(cmd: list[str], cwd=MIRROR_DIR, timeout=30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def main() -> int:
    if not MIRROR_DIR.is_dir():
        log_entry("error", f"mirror directory not found: {MIRROR_DIR}")
        return 1

    # 1. fetch from both remotes
    rc, out, err = run(["git", "fetch", "origin", "--prune"])
    if rc != 0:
        log_entry("error", f"git fetch origin failed", rc=rc, stderr=err)
        return 1

    rc, out, err = run(["git", "fetch", "github", "--prune"])
    if rc != 0:
        log_entry("error", f"git fetch github failed", rc=rc, stderr=err)
        return 1

    # 2. check divergence (commits on Codeberg that GitHub doesn't have)
    rc, behind_out, _ = run(["git", "rev-list", "--count", "github/main..origin/main"])
    if rc != 0:
        log_entry("error", "could not count divergence", rc=rc)
        return 1

    behind_count = int(behind_out.strip()) if behind_out.strip().isdigit() else -1

    rc, ahead_out, _ = run(["git", "rev-list", "--count", "origin/main..github/main"])
    ahead_count = int(ahead_out.strip()) if ahead_out.strip().isdigit() else -1

    if behind_count == 0 and ahead_count == 0:
        log_entry("ok", "mirror in sync", behind=0, ahead=0)
        return 0

    if behind_count > 0:
        # Codeberg has commits GitHub doesn't — need to push
        # First ensure local main is on origin/main
        rc, _, err = run(["git", "checkout", "main"])
        rc2, _, err2 = run(["git", "merge", "--ff-only", "origin/main"])
        if rc2 != 0:
            log_entry("error", "ff-only merge from origin/main failed", stderr=err2)
            return 1

        rc_push, push_out, push_err = run(["git", "push", "github", "main"])
        if rc_push != 0:
            log_entry("error", f"github mirror push failed", stderr=push_err[:500],
                      behind=behind_count)
            # non-fatal — maybe resolved by next cron run
        else:
            log_entry("synced", f"pushed {behind_count} commits to GitHub mirror",
                      behind=behind_count, ahead=ahead_count)

        # Also push tags
        rc_tag, tag_out, tag_err = run(["git", "push", "github", "--tags"])
        if rc_tag != 0:
            log_entry("warn", "tag push had issues", stderr=tag_err[:300])
    else:
        log_entry("ok", "mirror in sync", behind=behind_count, ahead=ahead_count)

    # 3. Sync tags from Codeberg to GitHub
    rc, _, _ = run(["git", "push", "github", "--tags"])
    # best-effort, tags may already exist

    return 0


if __name__ == "__main__":
    sys.exit(main())
