#!/usr/bin/env python3
"""PyPI Auto-Unblocker — publishes latest version as soon as PYPI_TOKEN appears.

Created 2026-05-30 as part of marketing-workflow-audit structural repair.
PyPI v0.8.8 is built + twine-check PASSED but unpublished. 1,299 downloads/month
see the old README without Codeberg CTA. This is the single highest-ROI blocked
marketing action on the execution board.

Behavior:
- Checks for PYPI_TOKEN every 6 hours
- If token found → auto-publish immediately → notify via log
- If token NOT found → count days since first check
- After 3 days without token → write a prominent blocker escalation artifact
- After 7 days → write an urgent escalation
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
LOG_DIR = ROOT / "agents/marketing/logs"
STATE_PATH = LOG_DIR / "pypi_auto_unblocker_state.json"
BLOCKER_ARTIFACT = ROOT / "drafts/pypi_blocker_escalation_latest.md"

PYPI_TOKEN_VAR = "PYPI_TOKEN"
TWINE_PASSWORD_VAR = "TWINE_PASSWORD"
TWINE_TOKEN_VAR = "TWINE_TOKEN"


def has_token() -> bool:
    return bool(
        os.environ.get(PYPI_TOKEN_VAR)
        or os.environ.get(TWINE_PASSWORD_VAR)
        or os.environ.get(TWINE_TOKEN_VAR)
    )


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {
        "first_check_ts": None,
        "last_check_ts": None,
        "token_was_available": False,
        "days_without_token": 0,
        "escalation_level": 0,  # 0=none, 1=3-day, 2=7-day
        "publish_attempted_for_version": None,
        "last_publish_success": None,
    }


def save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def write_escalation(state: dict, level: int) -> None:
    """Write a prominent blocker escalation artifact to the execution board."""
    now = datetime.now().isoformat()
    if level == 1:
        urgency = "⚠️ **3-DAY ESCALATION**"
        detail = (
            "PyPI v0.8.8 has been built and ready for 3 days but remains unpublished "
            "due to missing PYPI_TOKEN. 1,299 downloads/month see the old README "
            "with no Codeberg → star/watch/fork CTA path. Each download is a "
            "missed conversion opportunity."
        )
    else:
        urgency = "🚨 **URGENT 7-DAY ESCALATION**"
        detail = (
            "PyPI v0.8.8 has been built and ready for 7+ days. The missing PYPI_TOKEN "
            "is now the #1 structural blocker between the marketing system and real "
            "Codeberg adoption. 1,299 monthly downloads are landing on an outdated "
            "README without a primary-repo CTA. This should be the next human action, "
            "before any other manual lane work."
        )

    content = f"""# PyPI Blocker Escalation
Generated: {now}

{urgency}

**Blocked action:** Publish v0.8.8 to PyPI (wheel + sdist built, twine-check PASSED)
**Days without token:** {state.get('days_without_token', 'unknown')}
**Monthly downloads affected:** ~1,299 (seeing old README without Codeberg CTA)
**What's needed:** Set `PYPI_TOKEN` environment variable to a valid PyPI API token
  with upload scope for the `ralph-workflow` package.

{detail}

## How to fix (human action required)
1. Go to https://pypi.org/manage/account/token/
2. Create a token with scope: Upload for `ralph-workflow`
3. Set in this environment:
   ```
   export PYPI_TOKEN=pypi-xxxxxxxxxxxxxxxxxxxx
   ```
4. The auto-unblocker will detect the token within 6 hours and publish automatically.
   Or run manually: `python3 /home/mistlight/.openclaw/workspace/agents/marketing/pypi_auto_unblocker.py`

## Token was last checked
{now}

## Codeberg impact
- Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- PyPI page: https://pypi.org/project/ralph-workflow/
- Current version on PyPI: 0.8.7 (old README, no Codeberg forward-path CTA)
- Built awaiting publish: 0.8.8 (updated README with Codeberg primary CTA)
"""
    BLOCKER_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    BLOCKER_ARTIFACT.write_text(content)


def publish() -> tuple[bool, str]:
    """Attempt to publish the latest built version to PyPI."""
    dist_dir = ROOT / "dist"
    if not dist_dir.exists() or not list(dist_dir.glob("*.whl")):
        return False, "no built distribution found in dist/"
    
    pypi_token = (
        os.environ.get(PYPI_TOKEN_VAR)
        or os.environ.get(TWINE_TOKEN_VAR)
        or os.environ.get(TWINE_PASSWORD_VAR)
    )
    if not pypi_token:
        return False, "no PYPI_TOKEN available"
    
    env = os.environ.copy()
    if os.environ.get(PYPI_TOKEN_VAR):
        env["TWINE_USERNAME"] = "__token__"
        env["TWINE_PASSWORD"] = pypi_token
    elif os.environ.get(TWINE_TOKEN_VAR):
        env["TWINE_PASSWORD"] = pypi_token
    else:
        env["TWINE_PASSWORD"] = pypi_token
    
    try:
        result = subprocess.run(
            ["python3", "-m", "twine", "upload", "--non-interactive",
             str(dist_dir / "*.whl"), str(dist_dir / "*.tar.gz")],
            capture_output=True, text=True, env=env, timeout=120,
            cwd=str(ROOT),
        )
        if result.returncode == 0:
            return True, f"Published successfully. stdout: {result.stdout[:500]}"
        else:
            return False, f"twine upload failed: {result.stderr[:500]}"
    except Exception as e:
        return False, f"Error: {e}"


def run() -> None:
    now = datetime.now(timezone.utc)
    state = load_state()

    # Initialize first check timestamp if needed
    if state["first_check_ts"] is None:
        state["first_check_ts"] = now.isoformat()
    state["last_check_ts"] = now.isoformat()

    token_available = has_token()
    state["token_was_available"] = token_available

    if token_available:
        # Token found — attempt publish immediately
        print(f"[{now.isoformat()}] PYPI_TOKEN detected — attempting auto-publish...")
        success, msg = publish()
        if success:
            state["last_publish_success"] = now.isoformat()
            state["days_without_token"] = 0
            state["escalation_level"] = 0
            # Remove escalation artifact if it exists
            if BLOCKER_ARTIFACT.exists():
                BLOCKER_ARTIFACT.unlink()
            print(f"[{now.isoformat()}] ✅ Published to PyPI successfully!")
        else:
            print(f"[{now.isoformat()}] ❌ Publish failed: {msg}")
    else:
        # No token — count days and escalate
        first = datetime.fromisoformat(state["first_check_ts"])
        days = (now - first).days
        state["days_without_token"] = max(days, state.get("days_without_token", 0))

        if days >= 7 and state["escalation_level"] < 2:
            state["escalation_level"] = 2
            write_escalation(state, level=2)
            print(f"[{now.isoformat()}] 🚨 Escalation level 2: 7+ days without PYPI_TOKEN")
        elif days >= 3 and state["escalation_level"] < 1:
            state["escalation_level"] = 1
            write_escalation(state, level=1)
            print(f"[{now.isoformat()}] ⚠️ Escalation level 1: 3+ days without PYPI_TOKEN")
        else:
            # Refresh the escalation artifact with updated day count
            if state["escalation_level"] > 0:
                write_escalation(state, level=state["escalation_level"])
            print(f"[{now.isoformat()}] No PYPI_TOKEN — day {days} without token")

    save_state(state)


if __name__ == "__main__":
    run()
