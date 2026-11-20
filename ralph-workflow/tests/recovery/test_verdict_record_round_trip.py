from __future__ import annotations

import json
from pathlib import Path

from ralph.pipeline.checkpoint import load as load_checkpoint
from ralph.pipeline.state import PipelineState

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_a_v3_verdict_record_survives_a_checkpoint_save_and_load_unchanged": (
        "new coverage: the verdict record's schema version 3 was pinned through the "
        "migration ladder and the controller's own record, so a version-3 record loaded "
        "from a checkpoint was unproven; the checkpoint load route must return the record "
        "unchanged, with its probe reason and elapsed milliseconds intact"
    ),
}


def _v3_verdict_record() -> dict[str, object]:
    return {
        "schema_version": 3,
        "phase": "planning",
        "agent": "opencode",
        "verdict": "agent_dead",
        "facts": {},
        "attribution": {},
        "probe_outcome": "probe_could_not_run",
        "probe_reason": "probe_terminated_by_runtime:supervisor_idle:rc=9",
        "probe_elapsed_ms": 456,
        "termination": {
            "issuer": "supervisor_idle",
            "observed_returncode": 9,
            "reported_returncode": 9,
            "liveness_observed": None,
            "return_code_discrepancy": False,
            "log": [{"issuer": "supervisor_idle", "source": "idle timeout"}],
        },
        "typed_signal": None,
        "return_code_discrepancy": False,
    }


def test_a_v3_verdict_record_survives_a_checkpoint_save_and_load_unchanged(
    tmp_path: Path,
) -> None:
    record = _v3_verdict_record()
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps({"phase": "planning", "dead_agent_verdict_record": record}),
        encoding="utf-8",
    )

    state = load_checkpoint(checkpoint_path)

    assert state is not None
    loaded = state.dead_agent_verdict_record
    assert loaded == record
    assert loaded["schema_version"] == 3
    assert loaded["probe_reason"] == "probe_terminated_by_runtime:supervisor_idle:rc=9"
    assert loaded["probe_elapsed_ms"] == 456
    validated = PipelineState.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))
    assert validated.dead_agent_verdict_record == record
