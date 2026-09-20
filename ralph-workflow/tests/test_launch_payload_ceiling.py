from __future__ import annotations

import pytest

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


def test_irreducible_payload_is_rejected() -> None:
    with pytest.raises(OSError, match='argv\\+env ='):
        prepare_spawn_command(('claude', '--', 'x' * 100), cwd=None, env={'A': 'y' * 100}, payload_limit=32)
