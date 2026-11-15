from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ralph.process._agent_launch_error import AgentLaunchError
from ralph.process._spawn_validation import prepare_spawn_command

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_oversized_inline_prompt_is_replaced_by_file_path": (
        "spawn validation spills oversized prompts directly to .agent/tmp/prompt_<digest>_argv.md "
        "without materializing through the command builders module; updated test to verify real disk "
        "spill in a writable workspace directory rather than monkeypatching the retired seam"
    ),
}


def test_oversized_inline_prompt_is_replaced_by_file_path(tmp_path: Path) -> None:
    digest_placeholder = "a" * 16
    expected_path_len = len(
        str(tmp_path / ".agent" / "tmp" / f"prompt_{digest_placeholder}_argv.md")
    )
    payload_limit = expected_path_len + 40
    text = "x" * (payload_limit + 100)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    command = prepare_spawn_command(
        ("claude", "--", text), cwd=str(tmp_path), env={}, payload_limit=payload_limit
    )

    expected = str(tmp_path / ".agent" / "tmp" / f"prompt_{digest}_argv.md")
    assert command == ("claude", "--", expected)
    assert Path(expected).read_text(encoding="utf-8") == text


def test_irreducible_payload_regression_is_a_typed_runtime_launch_error() -> None:
    with pytest.raises(AgentLaunchError, match="argv\\+env =") as excinfo:
        prepare_spawn_command(
            ("claude", "--", "x" * 100), cwd=None, env={"A": "y" * 100}, payload_limit=32
        )

    assert excinfo.value.failure_origin == "runtime_launch"
