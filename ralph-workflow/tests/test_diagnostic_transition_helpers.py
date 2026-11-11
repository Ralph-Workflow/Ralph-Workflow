from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_EMIT_FILES = (
    REPO_ROOT / "ralph" / "pipeline" / "_integration_diagnostic.py",
    REPO_ROOT / "ralph" / "pipeline" / "_pending_merge_repair.py",
)
_HELPER_FILE = _EMIT_FILES[0]


def test_no_direct_diagnostic_transition_emit_outside_helpers() -> None:
    helper_src = _HELPER_FILE.read_text(encoding="utf-8")
    helper_start = helper_src.index("def _bump_attempt(")
    helper_end = helper_src.index("def diagnostic_needed(")
    allowed = helper_src[helper_start:helper_end]
    emit_re = re.compile(r"^\s+(RAISE_DIAGNOSTIC_INTERVENTION|BUMP_DIAGNOSTIC_ATTEMPT),$")
    for path in _EMIT_FILES:
        depth = 0
        src_lines = path.read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            in_import_parens = depth > 0
            depth += line.count("(") - line.count(")")
            if not in_import_parens and emit_re.match(line):
                assert path == _HELPER_FILE and line in allowed, (
                    f"{path.name}: direct transition emit outside helpers: {line!r}"
                )


def test_no_producer_of_retired_intervention_transition() -> None:
    scanned = (
        *_EMIT_FILES,
        REPO_ROOT / "ralph" / "pipeline" / "_integration_diagnostic_handoff.py",
    )
    for path in scanned:
        src = path.read_text(encoding="utf-8")
        assert "with_transition(\n        RAISE_DIAGNOSTIC_INTERVENTION" not in src, (
            f"{path.name}: RAISE_DIAGNOSTIC_INTERVENTION still has a producer"
        )


def test_bump_attempt_stamps_typed_budget_spent_at_cap() -> None:
    from ralph.pipeline._integration_diagnostic import (
        DIAGNOSTIC_BUDGET_SPENT_REASON,
        DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
        _bump_attempt,
    )
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
        merge_commit_repair_attempts=DIAGNOSTIC_REPAIR_ATTEMPT_CAP - 1,
    )
    bumped = _bump_attempt(record, "boom")
    assert bumped.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert bumped.diagnostic_intervention_required is False
    assert bumped.repair_cap_reason == DIAGNOSTIC_BUDGET_SPENT_REASON

    below = _bump_attempt(
        record.model_copy(
            update={"merge_commit_repair_attempts": DIAGNOSTIC_REPAIR_ATTEMPT_CAP - 2}
        ),
        "boom",
    )
    assert below.diagnostic_intervention_required is False
    assert below.repair_cap_reason is None


def test_pending_merge_repair_bump_routes_through_shared_helper() -> None:
    src = (REPO_ROOT / "ralph" / "pipeline" / "_pending_merge_repair.py").read_text(
        encoding="utf-8"
    )
    assert "from ralph.pipeline._integration_diagnostic import _bump_attempt" in src
    assert "BUMP_DIAGNOSTIC_ATTEMPT" not in src
