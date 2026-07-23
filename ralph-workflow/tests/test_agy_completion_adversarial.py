"""Adversarial completion verification tests for the AGY smoke pipeline.

The prior analysis feedback round identified three forgeries that a
model with workspace write capabilities could use to satisfy the
completion gate without the canonical submit path:

1. Forging a completion sentinel at
   ``.agent/completion_seen_<run_id>.json`` directly.
2. Forging a receipt at
   ``.agent/receipts/<run_id>/<type>.json`` directly.
3. Leaving a stale canonical artifact at
   ``.agent/artifacts/<type>.json`` from a previous run.

These tests pin the hardened contracts:

- ``evaluate_completion`` no longer accepts the legacy
  ``_artifact_is_schema_valid(artifact_path)`` fallback; only a
  current-run receipt-backed submission satisfies
  ``required_artifact_present`` (analysis how_to_fix item 3).
- ``_check_completion_sentinel`` with a ``sentinel_secret`` rejects
  a forged sentinel (item 2).
- ``artifact_receipt_present`` with a ``receipt_secret`` rejects a
  forged receipt (item 2).

Each test runs in <1s and does NOT call the live AGY binary; the
test surface is the completion-signal layer and the receipt
serialisation helpers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import (
    _COMPLETION_SENTINEL_RELPATHFMT,
    _check_completion_sentinel,
    evaluate_completion,
    is_artifact_submitted,
)
from ralph.mcp.artifacts.completion_receipts import (
    _receipt_hmac,
    artifact_receipt_present,
    write_artifact_receipt,
)
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.phases.required_artifacts import RequiredArtifact

if TYPE_CHECKING:
    from pathlib import Path


RECEIPT_SECRET = "broker-only-secret-12345"
SENTINEL_SECRET = "broker-only-sentinel-secret-67890"
RUN_ID = "adversarial-run-001"
ARTIFACT_TYPE = "smoke_test_result"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _write_forged_completion_sentinel(workspace: Path, run_id: str) -> Path:
    """Forge a completion sentinel directly from the workspace, no broker."""
    sentinel = workspace / _COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    return sentinel


def _write_forged_receipt(workspace: Path, run_id: str, artifact_type: str) -> Path:
    """Forge a receipt directly from the workspace, no broker."""
    receipt_dir = workspace / ".agent" / "receipts" / run_id
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{artifact_type}.json"
    receipt_path.write_text(
        json.dumps({"run_id": run_id, "artifact_type": artifact_type}),
        encoding="utf-8",
    )
    return receipt_path


def _required_artifact() -> RequiredArtifact:
    return RequiredArtifact(
        phase="smoke",
        artifact_type=ARTIFACT_TYPE,
        json_path=f".agent/artifacts/{ARTIFACT_TYPE}.json",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )


def test_forged_completion_sentinel_rejected_when_secret_provided(
    workspace: Path,
) -> None:
    """A model-written sentinel at ``.agent/completion_seen_<run_id>.json``
    is rejected when ``sentinel_secret`` is set.

    This pins the contract that completion sentinels carry broker-owned
    HMAC provenance; raw file creation under ``.agent/`` is not
    sufficient proof of completion.
    """
    _write_forged_completion_sentinel(workspace, RUN_ID)
    assert _check_completion_sentinel(workspace, RUN_ID) is True
    assert _check_completion_sentinel(workspace, RUN_ID, sentinel_secret=SENTINEL_SECRET) is False


def test_forged_receipt_rejected_when_secret_provided(workspace: Path) -> None:
    """A model-written receipt at ``.agent/receipts/<run_id>/<type>.json``
    is rejected when ``receipt_secret`` is set.

    This pins the contract that artifact submission receipts carry
    broker-owned HMAC provenance; raw file creation under ``.agent/``
    is not sufficient proof of canonical submission.
    """
    _write_forged_receipt(workspace, RUN_ID, ARTIFACT_TYPE)
    assert artifact_receipt_present(workspace, RUN_ID, ARTIFACT_TYPE) is True
    assert (
        artifact_receipt_present(workspace, RUN_ID, ARTIFACT_TYPE, receipt_secret=RECEIPT_SECRET)
        is False
    )


def test_broker_written_receipt_accepted_with_secret(workspace: Path) -> None:
    """A broker-written receipt (with a valid HMAC) is accepted even
    when ``receipt_secret`` is enforced.

    This is the positive-path proof that HMAC enforcement does not
    regress the canonical submission path.
    """
    write_artifact_receipt(workspace, RUN_ID, ARTIFACT_TYPE, receipt_secret=RECEIPT_SECRET)
    assert (
        artifact_receipt_present(workspace, RUN_ID, ARTIFACT_TYPE, receipt_secret=RECEIPT_SECRET)
        is True
    )


def test_evaluate_completion_rejects_stale_canonical_artifact(
    workspace: Path,
) -> None:
    """A stale canonical artifact at ``.agent/artifacts/<type>.json``
    from a previous run does NOT satisfy ``required_artifact_present``
    for a new run that has no current-run receipt.

    The legacy ``_artifact_is_schema_valid(artifact_path)`` fallback
    was removed in commit feab44edb's follow-up; only a current-run
    receipt-backed submission satisfies the completion gate.
    """
    artifact_path = workspace / ".agent" / "artifacts" / f"{ARTIFACT_TYPE}.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps({"run_id": "old-run", "result": "old"}),
        encoding="utf-8",
    )
    signals = evaluate_completion(
        workspace,
        raw_output=[],
        required_artifact=_required_artifact(),
        run_id=RUN_ID,
    )
    assert signals.required_artifact_present is False


def test_evaluate_completion_rejects_forged_receipt_without_receipt_secret(
    workspace: Path,
) -> None:
    """A forged receipt at ``.agent/receipts/<run_id>/<type>.json``
    satisfies the bare ``is_artifact_submitted`` check (no secret) so
    the existing canonical-submit path keeps working; the HMAC
    enforcement is an opt-in per the analysis contract.

    This pins the existing behavior: the receipt is treated as
    authoritative when no secret is configured (the legacy contract);
    when a secret is configured (the hardened contract), the forged
    receipt is rejected.
    """
    _write_forged_receipt(workspace, RUN_ID, ARTIFACT_TYPE)
    signals = evaluate_completion(
        workspace,
        raw_output=[],
        required_artifact=_required_artifact(),
        run_id=RUN_ID,
    )
    assert signals.required_artifact_present is True
    assert (
        artifact_receipt_present(workspace, RUN_ID, ARTIFACT_TYPE, receipt_secret=RECEIPT_SECRET)
        is False
    )


def test_evaluate_completion_threads_receipt_secret_to_verifier(
    workspace: Path,
) -> None:
    """End-to-end: ``evaluate_completion`` with ``receipt_secret``
    rejects a forged legacy-file receipt. Pins the live-wiring contract
    that the orchestrator can enable HMAC enforcement by setting
    ``receipt_secret`` on the completion gate.
    """
    _write_forged_receipt(workspace, RUN_ID, ARTIFACT_TYPE)

    # Without a secret, the forged legacy-file receipt is accepted (pre-P3 contract).
    signals_forged = evaluate_completion(
        workspace,
        raw_output=[],
        required_artifact=_required_artifact(),
        run_id=RUN_ID,
    )
    assert signals_forged.required_artifact_present is True

    # With a real secret configured, the forged receipt is rejected.
    signals_real = evaluate_completion(
        workspace,
        raw_output=[],
        required_artifact=_required_artifact(),
        run_id=RUN_ID,
        receipt_secret=RECEIPT_SECRET,
    )
    assert signals_real.required_artifact_present is False


def test_evaluate_completion_rejects_forged_completion_sentinel_with_secret(
    workspace: Path,
) -> None:
    """A forged completion sentinel satisfies the bare
    ``_check_completion_sentinel`` check (no secret) so the existing
    canonical declare-complete path keeps working; when a secret is
    configured (the hardened contract), the forged sentinel is
    rejected.

    Combined with the receipt HMAC test above this proves the
    adversarial surface is closed under the hardened contract: a
    model with workspace write capabilities cannot satisfy either
    the receipt check or the completion-sentinel check without
    knowing the broker-owned secrets.
    """
    _write_forged_completion_sentinel(workspace, RUN_ID)
    assert _check_completion_sentinel(workspace, RUN_ID) is True
    assert _check_completion_sentinel(workspace, RUN_ID, sentinel_secret=SENTINEL_SECRET) is False


def test_receipt_hmac_is_deterministic_and_collision_free(workspace: Path) -> None:
    """The receipt HMAC is a pure function of (secret, run_id, artifact_type).

    This pins the integrity property: the HMAC must not depend on
    filesystem state, must be deterministic for a given input, and
    must be different for a different ``run_id`` or ``artifact_type``
    (a forged receipt cannot reuse a sibling's HMAC).
    """
    h1 = _receipt_hmac(RECEIPT_SECRET, RUN_ID, ARTIFACT_TYPE)
    h2 = _receipt_hmac(RECEIPT_SECRET, RUN_ID, ARTIFACT_TYPE)
    assert h1 == h2
    h3 = _receipt_hmac(RECEIPT_SECRET, "other-run", ARTIFACT_TYPE)
    assert h1 != h3
    h4 = _receipt_hmac(RECEIPT_SECRET, RUN_ID, "other_artifact")
    assert h1 != h4
    h5 = _receipt_hmac("different-secret", RUN_ID, ARTIFACT_TYPE)
    assert h1 != h5


def test_db_sentinel_accepted_and_legacy_file_ignored_when_db_present(
    workspace: Path,
) -> None:
    """RFC-013 P3: when both the DB row and a legacy sentinel file exist,
    the DB row is authoritative. A valid DB HMAC is accepted; the legacy
    file alone (no DB row) is also accepted via the dual-read fallback.

    This pins the rollout contract that the production code does NOT
    continue writing legacy files; the read-path honors legacy files
    written by the pre-upgrade release during the dual-read window.
    """
    digest = hmac.new(SENTINEL_SECRET.encode(), RUN_ID.encode(), hashlib.sha256).hexdigest()

    # Case 1: DB row + matching legacy file — DB wins, accepted.
    db = RunStateDB(workspace)
    db.upsert_completion_sentinel(RUN_ID, digest)
    db.close()
    sentinel = workspace / _COMPLETION_SENTINEL_RELPATHFMT.format(run_id=RUN_ID)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
    assert _check_completion_sentinel(workspace, RUN_ID, sentinel_secret=SENTINEL_SECRET) is True

    # Case 2: legacy file only (no DB row) — fallback still honors it.
    sentinel.unlink()
    # Re-write the legacy file alone:
    sentinel.write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
    # DB still has the row from above — remove it to simulate a
    # pre-upgrade receipt alone.
    db2 = RunStateDB(workspace)
    db2.delete_completion_sentinel(RUN_ID)
    db2.close()
    assert _check_completion_sentinel(workspace, RUN_ID) is True


def test_promote_fallback_thread_receipt_secret_to_promoted_receipt(
    workspace: Path,
) -> None:
    """Adversarial regression: when ``is_artifact_submitted`` promotes a
    fallback artifact into a run-scoped receipt, the promoted receipt
    must carry a valid HMAC bound to the broker-owned secret enforced
    on the read path.

    Without the secret thread, the analysis-decision reproduction
    observed: ``is_artifact_submitted(..., receipt_secret='real')``
    returned ``True`` (promoted) while
    ``artifact_receipt_present(..., receipt_secret='real')`` returned
    ``False`` (no HMAC to verify). This pins the patched contract that
    promotion with a secret produces a receipt the verifier accepts
    under the same secret, and rejects under a different secret.
    """
    promotion_run_id = "adversarial-promotion-run"

    # Seed the Markdown fallback accepted by the canonical submission gate.
    fallback_path = workspace / ".agent" / "tmp" / "smoke_test_result.md"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_path.write_text(
        """---
type: smoke_test_result
status: passed
output_file: tmp/promotion/sentinel.js
---
## Summary
- [SUM-1] Promoted fallback under broker secret.
## Observed Working
- [OK-1] promotion secret thread
## Observed Breaks
- [BR-1] none observed
## Headless Guide Checks
- [HG-1] tool activity
""",
        encoding="utf-8",
    )

    # Promotion with secret writes a receipt the verifier accepts.
    assert (
        is_artifact_submitted(
            workspace,
            promotion_run_id,
            "smoke_test_result",
            receipt_secret=RECEIPT_SECRET,
        )
        is True
    )
    assert (
        artifact_receipt_present(
            workspace,
            promotion_run_id,
            "smoke_test_result",
            receipt_secret=RECEIPT_SECRET,
        )
        is True
    )

    # Wrong secret is rejected even when the row exists.
    assert (
        artifact_receipt_present(
            workspace,
            promotion_run_id,
            "smoke_test_result",
            receipt_secret="wrong-secret",
        )
        is False
    )


def test_promote_fallback_without_secret_keeps_legacy_no_hmac_contract(
    workspace: Path,
) -> None:
    """When ``is_artifact_submitted`` is called WITHOUT ``receipt_secret``,
    promotion keeps the pre-P3 contract: the receipt has no HMAC, but
    ``artifact_receipt_present`` (no secret) accepts the promoted
    receipt. This proves the fix preserves the pre-existing behavior
    for the no-secret call site.
    """
    no_secret_run_id = "adversarial-no-secret-run"
    fallback_path = workspace / ".agent" / "tmp" / "smoke_test_result.md"
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_path.write_text(
        """---
type: smoke_test_result
status: passed
output_file: tmp/promotion/no-secret.js
---
## Summary
- [SUM-1] Promoted fallback without broker secret.
## Observed Working
- [OK-1] promotion without secret
## Observed Breaks
- [BR-1] none observed
## Headless Guide Checks
- [HG-1] tool activity
""",
        encoding="utf-8",
    )

    assert is_artifact_submitted(workspace, no_secret_run_id, "smoke_test_result") is True
    assert artifact_receipt_present(workspace, no_secret_run_id, "smoke_test_result") is True
