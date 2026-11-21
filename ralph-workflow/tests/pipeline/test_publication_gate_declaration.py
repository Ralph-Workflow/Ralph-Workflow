from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.phases.required_artifacts import retry_hint_path
from ralph.pipeline.publication_gate import (
    PublicationBlock,
    PublicationGateRun,
    check_publication_gate,
    declared_publication_gate,
    leave_publication_gate_feedback,
    publication_gate_feedback,
    publication_gate_refusal,
)
from ralph.policy.loader import cached_default_policy_bundle

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout_seconds(10)

_EXPEDITIONS = (
    "RALPH-FACT: authoritative_verify_command: npm exec -- tsx script/verify/orchestrator.ts; "
    "its stage table is config/verify_stages.json5 (2026-10-31)"
)
_TREE = "a" * 40


def _declare(root: Path, line: str) -> Path:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(f"# Verification policy\n\n{line}\n", encoding="utf-8")
    return policy


class _NeverRuns:
    def __call__(self, command: str, cwd: Path, timeout_seconds: float) -> PublicationGateRun:
        raise AssertionError(f"a misconfigured gate must not run: {command}")


def _tree(_root: Path, _commit: str) -> str:
    return _TREE


def test_a_declaration_with_trailing_prose_resolves_to_its_command(tmp_path: Path) -> None:
    _declare(tmp_path, _EXPEDITIONS)
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        command = declared_publication_gate(tmp_path)
        declared_publication_gate(tmp_path)
    finally:
        logger.remove(sink)

    assert command == "npm exec -- tsx script/verify/orchestrator.ts"
    dropped = [line for line in warnings if "dropped" in line]
    assert len(dropped) == 1
    assert "(2026-10-31)" in dropped[0]


def test_a_backtick_declaration_is_unchanged(tmp_path: Path) -> None:
    _declare(
        tmp_path,
        "RALPH-FACT: authoritative_verify_command: `make -C ralph-workflow verify` (runs it all).",
    )

    assert declared_publication_gate(tmp_path) == "make -C ralph-workflow verify"


def test_an_unparseable_declaration_blocks_as_a_policy_error_without_routing_back(
    tmp_path: Path,
) -> None:
    policy_file = _declare(
        tmp_path, "RALPH-FACT: authoritative_verify_command: run the (usual) checks"
    )
    policy = cached_default_policy_bundle().pipeline
    errors: list[str] = []
    sink = logger.add(lambda message: errors.append(str(message)), level="ERROR")
    try:
        first = check_publication_gate(tmp_path, "HEAD", runner=_NeverRuns(), tree_of=_tree)
        again = check_publication_gate(tmp_path, "HEAD", runner=_NeverRuns(), tree_of=_tree)
        refusal = publication_gate_refusal(tmp_path, "HEAD")
    finally:
        logger.remove(sink)

    assert first is not None
    assert again is not None
    assert first.block is PublicationBlock.GATE_FAILED
    assert first.routes_back_to_development is False
    assert first.invocation_error
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is None
    assert leave_publication_gate_feedback(tmp_path, policy) is False
    assert not (tmp_path / retry_hint_path("development", pipeline_policy=policy)).exists()
    assert len(errors) == 1
    assert f"{policy_file}:3" in errors[0]
    assert refusal is not None
    assert "misconfigured" in refusal[0].external_condition


@pytest.mark.parametrize(
    ("exit_code", "output"),
    [
        (2, 'sh: 1: Syntax error: "(" unexpected\n'),
        (127, "sh: 1: its: not found\n"),
    ],
)
def test_a_gate_that_never_started_its_command_is_an_invocation_error(
    tmp_path: Path, exit_code: int, output: str
) -> None:
    _declare(tmp_path, "RALPH-FACT: authoritative_verify_command: `make verify`")
    policy = cached_default_policy_bundle().pipeline

    def runner(_command: str, _cwd: Path, _timeout: float) -> PublicationGateRun:
        return PublicationGateRun(exit_code=exit_code, output=output)

    result = check_publication_gate(tmp_path, "HEAD", runner=runner, tree_of=_tree)

    assert result is not None
    assert result.invocation_error
    assert result.routes_back_to_development is False
    assert publication_gate_feedback(tmp_path, policy, "development_commit") is None


def test_a_real_red_result_with_exit_two_still_routes_back(tmp_path: Path) -> None:
    _declare(tmp_path, "RALPH-FACT: authoritative_verify_command: `make verify`")

    def runner(_command: str, _cwd: Path, _timeout: float) -> PublicationGateRun:
        return PublicationGateRun(exit_code=2, output="make: *** [test] Error 1\n")

    result = check_publication_gate(tmp_path, "HEAD", runner=runner, tree_of=_tree)

    assert result is not None
    assert result.invocation_error == ""
    assert result.routes_back_to_development is True
