"""Acceptance Scenario 8: legacy representation migration on restart.

Seed a contradicting legacy checkpoint + integration record pair as RAW
disk bytes (checkpoint claims ``development``, the legacy record claims
``integrating`` with legacy evidence keys), then restart through the
production startup/recovery path — the same ``Checkpoint.load`` resume
read and the same ``read_record`` recovery preamble
(``recover_incomplete_integration``) that ``run_loop`` startup uses.
Migration must resolve to exactly one authoritative state — explicit
intervention with business dispatch blocked for the legacy
repository-global ``ls-files`` record — and never merge contradictory
truths heuristically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.auto_integrate_recovery import (
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.checkpoint import Checkpoint
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.rebase_state import RebaseState

#: Legacy record payload: phase claims an in-flight integration while the
#: checkpoint below claims development, and the diagnostic evidence carries
#: the legacy repository-global ``ls-files`` key which must raise explicit
#: intervention rather than being silently migrated.
_LEGACY_INTERVENTION_PAYLOAD: dict[str, object] = {
    "phase": "integrating",
    "target": "main",
    "pre_feature_sha": "a" * 40,
    "pre_target_sha": "b" * 40,
    "diagnostic_evidence": [
        ["ls-files", "some_tracked_files"],
    ],
}

#: Legacy record payload with legacy dictionary evidence keys, which must
#: migrate cleanly to the new key space without intervention.
_LEGACY_DICT_EVIDENCE_PAYLOAD: dict[str, object] = {
    "phase": "integrating",
    "target": "main",
    "pre_feature_sha": "a" * 40,
    "pre_target_sha": "b" * 40,
    "diagnostic_evidence": {
        "work": "unstaged diff",
        "controls": "controls data",
    },
}

_CONFIG = UnifiedConfig.model_validate(
    {
        "general": {
            "auto_integrate_target": "main",
            "auto_integrate_remote_enabled": False,
        }
    }
)


def _seed_raw_legacy_pair(
    root: Path,
    payload: dict[str, object],
    *,
    checkpoint_payload: dict[str, object] | None = None,
) -> None:
    """Write raw legacy checkpoint + record bytes directly under ``root``.

    Bypasses ``Checkpoint.save`` and ``write_record`` so the production
    resume read (``Checkpoint.load``) and the recovery preamble
    (``read_record`` inside ``recover_incomplete_integration``) must
    parse and migrate the legacy bytes on restart, exactly as a real
    process restart does.
    """
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "checkpoint.json").write_text(
        json.dumps(checkpoint_payload or {"phase": "development"}), encoding="utf-8"
    )
    (agent_dir / "auto_integrate_in_progress.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _restart_via_production_recovery(root: Path) -> tuple[RebaseState, Path]:
    """Restart via the production startup/recovery seam.

    Loads the pipeline checkpoint through the same resume path
    ``run._load_configuration`` uses and runs the same recovery preamble
    ``run_loop._run_auto_integrate_recovery_preamble`` runs before the
    dispatch loop.
    """
    checkpoint_path = root / ".agent" / "checkpoint.json"
    reloaded = Checkpoint(checkpoint_path).load()
    assert reloaded is not None
    assert reloaded.phase == "development"

    recovered = recover_incomplete_integration(WorkspaceScope(root), config=_CONFIG)
    assert recovered is not None
    return recovered, checkpoint_path


def test_legacy_restart_intervention_record_is_single_authority(tmp_git_repo: Path) -> None:
    """Contradicting legacy pair with global evidence resolves to explicit
    intervention through the production recovery path; the record wins over
    the checkpoint's ``development`` and blocks business dispatch."""
    _seed_raw_legacy_pair(tmp_git_repo, _LEGACY_INTERVENTION_PAYLOAD)

    recovered, _ = _restart_via_production_recovery(tmp_git_repo)

    # (a) Recovery retained the durable record: exactly one authoritative
    # state, no merged hybrid truth between checkpoint and record.
    assert recovery_retained_record(recovered) is True

    # The legacy repository-global evidence forces explicit intervention,
    # never a heuristic merge that would drop the fingerprint. The raw
    # ``ls-files`` key is preserved for the operator.
    persisted = read_record(tmp_git_repo)
    assert persisted is not None
    assert persisted.phase == "integrating"
    assert persisted.diagnostic_intervention_required is True
    assert ("ls-files", "some_tracked_files") in persisted.diagnostic_evidence

    # (b) No business dispatch: the authoritative verdict over the recovery
    # outcome blocks ordinary phase dispatch while the record is retained.
    verdict = inspect_integration_resolution(tmp_git_repo, recovered)
    assert verdict.dispatch_allowed is False


def test_legacy_restart_clean_dict_evidence_is_single_authority(tmp_git_repo: Path) -> None:
    """Contradicting legacy pair with dictionary evidence migrates cleanly
    through the production recovery path; the record still blocks recovery
    and business dispatch as the single authority."""
    _seed_raw_legacy_pair(tmp_git_repo, _LEGACY_DICT_EVIDENCE_PAYLOAD)

    recovered, _ = _restart_via_production_recovery(tmp_git_repo)

    persisted = read_record(tmp_git_repo)
    assert persisted is not None
    assert persisted.phase == "integrating"
    assert not persisted.diagnostic_intervention_required
    evidence = dict(persisted.diagnostic_evidence)
    assert evidence.get("unstaged_work") == "unstaged diff"
    assert evidence.get("commit_controls") == "controls data"

    # The record is still the single recovery authority: it is retained and
    # ordinary dispatch stays blocked until recovery finishes.
    assert recovery_retained_record(recovered) is True
    verdict = inspect_integration_resolution(tmp_git_repo, recovered)
    assert verdict.dispatch_allowed is False


def test_policy_defaults_bundle_consistent(tmp_git_repo: Path) -> None:
    """The default policy bundle loads and a stale pre-seeded
    ``last_decision`` in the legacy checkpoint does not survive the
    production restart: the recovery preamble recomputes the authoritative
    decision from the retained record."""
    bundle = load_policy(Path(__file__).parents[2] / "ralph" / "policy" / "defaults")
    assert bundle.pipeline is not None

    _seed_raw_legacy_pair(
        tmp_git_repo,
        _LEGACY_INTERVENTION_PAYLOAD,
        checkpoint_payload={
            "phase": "development",
            "rebase": {"last_decision": "continue_phase"},
        },
    )
    recovered, _ = _restart_via_production_recovery(tmp_git_repo)

    # A stale pre-seeded last_decision must not survive the restart: the
    # recovery outcome recomputes from current authority outputs only.
    assert recovered.last_decision != "continue_phase"
    assert recovered.last_decision == "block_for_recovery"
    assert recovery_retained_record(recovered) is True
    verdict = inspect_integration_resolution(tmp_git_repo, recovered)
    assert verdict.dispatch_allowed is False
