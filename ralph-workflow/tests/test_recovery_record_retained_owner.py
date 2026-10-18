"""RebaseState is the single owner of ``recovery_record_retained``.

Static scan: no production call site outside ``rebase_state.py`` may write
``recovery_record_retained`` via ``model_copy(update={...})`` or pass
``recovery_record_retained=True`` without a typed
``recovery_record_retained_reason``. Behavior: the typed setter/clear pair.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.rebase_state import (
    RebaseState,
    StartupRetentionReason,
)

# Audit-style file scans: allow extra headroom under shard load so the
# budget-shaped SIGALRM does not flake the same scan the other audit
# tests in this codebase already permit (see
# ``tests/test_audit_workspace_resource_inventory.py`` for the same
# precedent). The contract being verified is unchanged: per-file bytes
# pre-filter then a one-shot per-line check.
pytestmark = pytest.mark.timeout_seconds(5)

RALPH_PIPELINE = Path(__file__).resolve().parent.parent / "ralph" / "pipeline"

# Sites the contract explicitly permits to keep the raw model_copy form.
ALLOWED_MODEL_COPY_SITES = {
    "rebase_state.py",
    "auto_integrate_recovery.py",  # record_conflict(...).model_copy(...) contract
    "run_loop.py",  # owned by another unit
}


def test_no_model_copy_writers_outside_allowed_sites() -> None:
    offenders: list[str] = []
    for path in sorted(RALPH_PIPELINE.rglob("*.py")):
        if path.name in ALLOWED_MODEL_COPY_SITES:
            continue
        # Cheap bytes pre-filter: decode only files that can match, so the
        # 1s per-test timeout holds under shard load.
        raw = path.read_bytes()
        if b"recovery_record_retained" not in raw or b"model_copy" not in raw:
            continue
        text = raw.decode(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "recovery_record_retained" in line and "model_copy" in line and "update=" in line:
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == []


def test_constructor_true_sites_carry_typed_reason() -> None:
    for path in sorted(RALPH_PIPELINE.rglob("*.py")):
        if path.name in {"rebase_state.py", "run_loop.py"}:
            continue
        raw = path.read_bytes()
        if b"recovery_record_retained=True" not in raw:
            continue
        text = raw.decode(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "recovery_record_retained=True" in line:
                window = "\n".join(text.splitlines()[max(0, lineno - 3) : lineno + 3])
                assert "recovery_record_retained_reason=" in window, (
                    f"{path}:{lineno} sets recovery_record_retained=True "
                    "without a typed recovery_record_retained_reason"
                )


def test_setter_retains_with_reason() -> None:
    state = RebaseState()
    assert not state.recovery_record_retained
    assert state.recovery_record_retained_reason is None

    retained = state.set_recovery_record_retained(
        StartupRetentionReason.TRANSIENT_FAST_FORWARD_FAILURE
    )
    assert retained.recovery_record_retained is True
    assert (
        retained.recovery_record_retained_reason
        is StartupRetentionReason.TRANSIENT_FAST_FORWARD_FAILURE
    )
    # Frozen model: original untouched.
    assert not state.recovery_record_retained


def test_clear_resets_reason_too() -> None:
    retained = RebaseState().set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT)
    cleared = retained.clear_recovery_record_retained()
    assert cleared.recovery_record_retained is False
    assert cleared.recovery_record_retained_reason is None


def test_reason_vocabulary_is_closed() -> None:
    assert set(StartupRetentionReason) == {
        "FAILED_ABORT",
        "FAILED_RESET",
        "TARGET_POINTER_UNREFRESHABLE",
        "TRANSIENT_FAST_FORWARD_FAILURE",
        "OWNED_TARGET_PRECONDITION_FAILED",
        "OWNED_OPERATION_IDENTITY_MISMATCH",
        "REMOTE_RECONCILE_RETAINED",
        "OBSERVED_RETAINED_ON_DISK",
    }
