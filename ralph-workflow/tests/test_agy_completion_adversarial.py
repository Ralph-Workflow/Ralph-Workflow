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
    sentinel = workspace / _COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    return sentinel


def _write_forged_receipt(workspace: Path, run_id: str, artifact_type: str) -> Path:
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
        artifact_path=f".agent/artifacts/{ARTIFACT_TYPE}.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )


def test_forged_completion_sentinel_rejected_when_secret_provided(
    workspace: Path,
) -> None:
    _write_forged_completion_sentinel(workspace, RUN_ID)
    assert _check_completion_sentinel(workspace, RUN_ID) is True
    assert _check_completion_sentinel(workspace, RUN_ID, sentinel_secret=SENTINEL_SECRET) is False


def test_forged_receipt_rejected_when_secret_provided(workspace: Path) -> None:
    _write_forged_receipt(workspace, RUN_ID, ARTIFACT_TYPE)
    assert artifact_receipt_present(workspace, RUN_ID, ARTIFACT_TYPE) is True
    assert (
        artifact_receipt_present(workspace, RUN_ID, ARTIFACT_TYPE, receipt_secret=RECEIPT_SECRET)
        is False
    )


def test_broker_written_receipt_accepted_with_secret(workspace: Path) -> None:
    write_artifact_receipt(workspace, RUN_ID, ARTIFACT_TYPE, receipt_secret=RECEIPT_SECRET)
    assert (
        artifact_receipt_present(workspace, RUN_ID, ARTIFACT_TYPE, receipt_secret=RECEIPT_SECRET)
        is True
    )


def test_evaluate_completion_rejects_stale_canonical_artifact(
    workspace: Path,
) -> None:
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
    assert signals.required_artifact_present.holds is False


def test_evaluate_completion_rejects_forged_receipt_without_receipt_secret(
    workspace: Path,
) -> None:
    _write_forged_receipt(workspace, RUN_ID, ARTIFACT_TYPE)
    signals = evaluate_completion(
        workspace,
        raw_output=[],
        required_artifact=_required_artifact(),
        run_id=RUN_ID,
    )
    assert signals.required_artifact_present.holds is True
    assert (
        artifact_receipt_present(workspace, RUN_ID, ARTIFACT_TYPE, receipt_secret=RECEIPT_SECRET)
        is False
    )


def test_evaluate_completion_threads_receipt_secret_to_verifier(
    workspace: Path,
) -> None:
    _write_forged_receipt(workspace, RUN_ID, ARTIFACT_TYPE)

    signals_forged = evaluate_completion(
        workspace,
        raw_output=[],
        required_artifact=_required_artifact(),
        run_id=RUN_ID,
    )
    assert signals_forged.required_artifact_present.holds is True

    signals_real = evaluate_completion(
        workspace,
        raw_output=[],
        required_artifact=_required_artifact(),
        run_id=RUN_ID,
        receipt_secret=RECEIPT_SECRET,
    )
    assert signals_real.required_artifact_present.holds is False


def test_evaluate_completion_rejects_forged_completion_sentinel_with_secret(
    workspace: Path,
) -> None:
    _write_forged_completion_sentinel(workspace, RUN_ID)
    assert _check_completion_sentinel(workspace, RUN_ID) is True
    assert _check_completion_sentinel(workspace, RUN_ID, sentinel_secret=SENTINEL_SECRET) is False


def test_receipt_hmac_is_deterministic_and_collision_free(workspace: Path) -> None:
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
    digest = hmac.new(SENTINEL_SECRET.encode(), RUN_ID.encode(), hashlib.sha256).hexdigest()

    db = RunStateDB(workspace)
    db.upsert_completion_sentinel(RUN_ID, digest)
    db.close()
    sentinel = workspace / _COMPLETION_SENTINEL_RELPATHFMT.format(run_id=RUN_ID)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
    assert _check_completion_sentinel(workspace, RUN_ID, sentinel_secret=SENTINEL_SECRET) is True

    sentinel.unlink()
    sentinel.write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
    db2 = RunStateDB(workspace)
    db2.delete_completion_sentinel(RUN_ID)
    db2.close()
    assert _check_completion_sentinel(workspace, RUN_ID) is True


def test_promote_fallback_thread_receipt_secret_to_promoted_receipt(
    workspace: Path,
) -> None:
    promotion_run_id = "adversarial-promotion-run"

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
