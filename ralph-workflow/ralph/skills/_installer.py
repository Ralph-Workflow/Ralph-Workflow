"""Skill installation via subprocess calls to the claude CLI."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

from ralph.skills._bundle import BASELINE_SKILL_BUNDLE, SkillInstallSpec
from ralph.skills._state import CapabilityEntry, CapabilityStatus


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def install_skill(spec: SkillInstallSpec, *, timeout: int = 120) -> bool:
    """Run `claude plugin install <plugin_id>` and return True on success."""
    result = subprocess.run(
        ["claude", "plugin", "install", spec.plugin_id],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return result.returncode == 0


def install_baseline_skills(
    bundle: tuple[SkillInstallSpec, ...] | None = None,
    *,
    timeout: int = 120,
) -> tuple[CapabilityEntry, list[str]]:
    """Install the baseline skill bundle and return an entry + failure list."""
    specs = bundle if bundle is not None else BASELINE_SKILL_BUNDLE
    successes: list[str] = []
    failures: list[str] = []
    for spec in specs:
        if install_skill(spec, timeout=timeout):
            successes.append(spec.plugin_id)
        else:
            failures.append(spec.plugin_id)

    if not failures:
        return (
            CapabilityEntry(
                status=CapabilityStatus.INSTALLED_HEALTHY, last_check_ok_iso=_now_iso()
            ),
            [],
        )
    if not successes:
        return (
            CapabilityEntry(status=CapabilityStatus.NEEDS_REPAIR, last_check_fail_iso=_now_iso()),
            failures,
        )
    return (
        CapabilityEntry(status=CapabilityStatus.INSTALLED_DEGRADED, last_check_ok_iso=_now_iso()),
        failures,
    )


def check_skills_update_available(*, timeout: int = 30) -> bool:
    """Return True if `claude plugin list` output indicates an update is available."""
    try:
        result = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return False
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace").lower()
        return "update available" in output or "updates available" in output
    except Exception:
        return False


__all__ = ["check_skills_update_available", "install_baseline_skills", "install_skill"]
