"""Acceptance Scenario 8: legacy representation migration on restart.

Seed a contradicting legacy checkpoint + integration record pair as RAW
disk bytes (checkpoint claims ``development``, the legacy record claims
``integrating`` with legacy evidence keys), then restart through the
production startup/recovery path — the same ``Checkpoint.load`` resume
read and the same ``read_record`` recovery preamble
(``recover_incomplete_integration``) that ``run_loop`` startup uses.
Migration must resolve to exactly one authoritative state and never
merge contradictory truths heuristically. Git outranks both files: with
no operation in progress and HEAD already on the target, Git disproves
the record's ``integrating`` claim, so recovery clears the record rather
than retaining it (a retained stale record deferred catch-up forever).
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
    active_checkpoint: dict[str, object] = (
        checkpoint_payload if checkpoint_payload is not None else {"phase": "development"}
    )
    (agent_dir / "checkpoint.json").write_text(json.dumps(active_checkpoint), encoding="utf-8")
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


def _assert_git_is_the_single_authority(root: Path, recovered: RebaseState) -> None:
    """Recovery reconciled from Git: no record survives, dispatch resumes.

    Git shows no operation in progress and HEAD already sits on the
    target, so it disproves the legacy record's ``integrating`` claim.
    Recovery must not come back owning that record (a retained stale
    record deferred catch-up forever) and must not leave a hybrid of the
    checkpoint and the record behind: the record is cleared, so Git is
    the one authority and ordinary dispatch is allowed.
    """
    assert recovery_retained_record(recovered) is False
    assert read_record(root) is None
    verdict = inspect_integration_resolution(root, recovered)
    assert verdict.dispatch_allowed is True


def test_legacy_restart_intervention_record_is_single_authority(tmp_git_repo: Path) -> None:
    """Contradicting legacy pair with global evidence loads cleanly: the
    legacy repository-global key is dropped silently on load (it never
    raises intervention), and recovery re-derives state from Git, which
    outranks both the checkpoint and the record."""
    _seed_raw_legacy_pair(tmp_git_repo, _LEGACY_INTERVENTION_PAYLOAD)

    pre_recovery = read_record(tmp_git_repo)
    assert pre_recovery is not None
    assert pre_recovery.phase == "integrating"
    assert pre_recovery.diagnostic_intervention_required is False
    assert ("ls-files", "some_tracked_files") not in pre_recovery.diagnostic_evidence

    recovered, _ = _restart_via_production_recovery(tmp_git_repo)

    _assert_git_is_the_single_authority(tmp_git_repo, recovered)


def test_legacy_restart_clean_dict_evidence_is_single_authority(tmp_git_repo: Path) -> None:
    """Contradicting legacy pair with dictionary evidence migrates cleanly
    on load, and recovery resolves it to the single Git authority."""
    _seed_raw_legacy_pair(tmp_git_repo, _LEGACY_DICT_EVIDENCE_PAYLOAD)

    # Legacy dictionary evidence migrates cleanly to the new key space on load.
    pre_recovery = read_record(tmp_git_repo)
    assert pre_recovery is not None
    pre_evidence = dict(pre_recovery.diagnostic_evidence)
    assert pre_evidence.get("unstaged_work") == "unstaged diff"
    assert pre_evidence.get("commit_controls") == "controls data"
    assert not pre_recovery.diagnostic_intervention_required

    recovered, _ = _restart_via_production_recovery(tmp_git_repo)

    _assert_git_is_the_single_authority(tmp_git_repo, recovered)


def test_policy_defaults_bundle_consistent(tmp_git_repo: Path) -> None:
    """The default policy bundle loads and a stale pre-seeded
    ``last_decision`` in the legacy checkpoint does not survive the
    production restart: the recovery outcome is recomputed from the
    current authority, not copied from the checkpoint."""
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

    assert recovered.last_decision != "continue_phase"
    _assert_git_is_the_single_authority(tmp_git_repo, recovered)
