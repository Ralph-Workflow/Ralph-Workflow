from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution.chain_candidates import session_ledger
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import Cooldown

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.policy.models import PolicyBundle

_CONFLICTED = ["a.py", "b.py", "c.py"]
_POLICY_BUNDLE = load_policy(Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults")
_CONFIG = UnifiedConfig.model_validate({"general": {}})


def _policy_bundle() -> PolicyBundle:
    return _POLICY_BUNDLE


def _cooling(session: ResolutionSession, candidates: tuple[str, ...]) -> dict[str, Cooldown]:
    ledger = session_ledger(session)
    if ledger is None:
        return {}
    cooling: dict[str, Cooldown] = {}
    for name in candidates:
        status = ledger.status(PHASE_RESOLUTION, name)
        if isinstance(status, Cooldown):
            cooling[name] = status
    return cooling


def _advance_past(clock: FakeClock, status: Cooldown) -> None:
    clock.advance(max(0, status.until_ms - int(clock.monotonic() * 1000)) / 1000.0)


def _install_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unmerged: Sequence[str] = _CONFLICTED,
) -> None:
    from ralph.git.conflict_stage_entries import ConflictStageEntries
    from ralph.pipeline.conflict_resolution import sight as sight_module

    monkeypatch.setattr(driver_module, "unmerged_paths", lambda root: list(unmerged))
    monkeypatch.setattr(
        driver_module,
        "paths_with_conflict_markers",
        lambda root, paths: list(unmerged),
    )
    monkeypatch.setattr(
        driver_module, "resolution_chain_agents", lambda _bundle: ("primary", "fallback")
    )
    monkeypatch.setattr(
        driver_module,
        "render_conflict_prompt",
        lambda *, root, **_kwargs: root / "resolution-prompt.md",
    )
    monkeypatch.setattr(driver_module, "_restore_status_bar", lambda *_args: None)
    monkeypatch.setattr(driver_module, "_sleep_seconds", lambda _seconds: None)
    monkeypatch.setattr(
        sight_module,
        "conflict_stage_entries",
        lambda _root, _paths: ConflictStageEntries({}),
    )
