from __future__ import annotations

import pytest

from ralph.process._agent_launch_error import AgentLaunchError
from ralph.process._spawn_validation import prepare_spawn_command


def test_oversized_inline_prompt_is_replaced_by_file_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'ralph.agents.invoke._command_builders.materialize_argv_prompt',
        lambda _text, _cwd: '/workspace/.agent/tmp/prompt_argv.md',
    )

    command = prepare_spawn_command(
        ('claude', '--', 'x' * 100), cwd='/workspace', env={}, payload_limit=60
    )

    assert command == ('claude', '--', '/workspace/.agent/tmp/prompt_argv.md')


def test_irreducible_payload_regression_is_a_typed_runtime_launch_error() -> None:
    """S-2: validation-side E2BIG has the same typed contract as Popen E2BIG."""
    with pytest.raises(AgentLaunchError, match='argv\\+env =') as excinfo:
        prepare_spawn_command(('claude', '--', 'x' * 100), cwd=None, env={'A': 'y' * 100}, payload_limit=32)

    assert excinfo.value.failure_origin == "runtime_launch"
