from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.recovery.false_cooldown_fixture import false_cooldown_fixture_draft
from tests.recovery._corpus_derive import load_corpus

if TYPE_CHECKING:
    from pathlib import Path


RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_false_cooldown_fixture module": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; module renamed from tests/recovery/test_false_bench_fixture.py with unchanged content"
    ),
}


def test_false_cooldown_fixture_draft_loads_and_preserves_termination_issuer(
    tmp_path: Path,
) -> None:
    event: dict[str, object] = {
        "agent": "codex",
        "transport": "codex",
        "phase": "development",
        "reason": "recorded reason",
        "config_fingerprint": "abc123",
        "verdict_ref": {
            "facts": {
                "workspace_changed": "false",
                "workspace_changed_by_tool": "false",
                "artifact_valid": False,
                "elapsed_to_terminal_ms": 1200,
                "exit_status": "killed_sigterm",
                "liveness_observed": True,
                "subagent_activity_observed": False,
            },
            "attribution": {
                "attribution": "runtime",
                "runtime_cause": "quota_detector",
                "cleared": True,
                "counts_toward_agent_state": False,
                "reason": "runtime-issued termination",
            },
            "termination": {
                "issuer": "quota_detector",
                "observed_returncode": -15,
                "reported_returncode": 143,
                "liveness_observed": True,
            },
        },
        "session_ref": {
            "run": "run-7",
            "phase": "development",
            "entry_point": "main_loop",
            "session_id": "session-9",
        },
    }

    draft = false_cooldown_fixture_draft(event)
    (tmp_path / "draft.json").write_text(json.dumps(draft), encoding="utf-8")
    cases = load_corpus(tmp_path)

    assert draft == {
        "id": "false_cooldown_codex_abc123",
        "label": "negative",
        "expected_cooldown": False,
        "expected_verdict": "not_agent_fault",
        "expected_attribution": "runtime",
        "agent": "codex",
        "transport": "codex",
        "workspace_changed": "false",
        "workspace_changed_by_tool": "false",
        "artifact_valid": False,
        "elapsed_to_terminal_ms": 1200,
        "exit_status": "killed_sigterm",
        "liveness_observed": True,
        "subagent_activity_observed": False,
        "termination": {
            "issuer": "quota_detector",
            "observed_returncode": -15,
            "reported_returncode": 143,
            "liveness_observed": True,
        },
        "captured_lines": [],
        "source_session": {
            "run": "run-7",
            "phase": "development",
            "entry_point": "main_loop",
            "session_id": "session-9",
        },
        "notes": "A reviewer fills captured_lines from source_session before committing.",
    }
    assert len(cases) == 1
    assert cases[0].facts.termination is not None
    assert cases[0].facts.termination.issuer.value == "quota_detector"


def test_false_cooldown_fixture_draft_rejects_missing_verdict_reference() -> None:
    with pytest.raises(ValueError, match="verdict_ref"):
        false_cooldown_fixture_draft(
            {
                "agent": "codex",
                "transport": "codex",
                "config_fingerprint": "abc123",
                "session_ref": {},
            }
        )
