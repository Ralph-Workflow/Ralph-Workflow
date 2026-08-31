"""Regression coverage for the opencode ``-m`` model-flag contract.

Two defects were measured against the installed opencode 1.18.25 and are
pinned here:

1. The ``opencode/`` **alias** prefix was removed three times -- once at
   alias resolution (correct) and twice more downstream. opencode itself
   publishes a provider literally named ``opencode`` (``opencode models``
   lists ``opencode/big-pickle``, ``opencode/nemotron-3-ultra-free``, ...),
   and its ``run`` handler parses ``-m`` as ``provider/model``. The extra
   strips turned the alias ``opencode/opencode/big-pickle`` into
   ``-m big-pickle``, which opencode reads as ``providerID='big-pickle'``
   with an empty model id, making every model under that provider
   unselectable. The same over-stripping removed the ``/`` before the
   local-model preflight could see it, so the run failed at the CLI
   instead of failing fast with a diagnostic.

2. The alias-built ``-m <model>`` flag was interpolated without
   ``shlex.quote`` and re-tokenized with ``str.split()``, so an alias
   carrying whitespace smuggled extra argv tokens (``--agent plan``) onto
   the opencode command line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.agents.registry import AgentRegistry
from ralph.api.opencode import opencode_model_id_from_flag
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


def _argv_for_alias(alias: str, tmp_path: Path) -> list[str]:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("do the thing", encoding="utf-8")
    config = AgentRegistry().get(alias)
    assert config is not None, f"registry did not resolve {alias!r}"
    return build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )


def _model_value(argv: list[str]) -> str:
    assert "-m" in argv, argv
    return argv[argv.index("-m") + 1]


def test_opencode_regression_provider_named_opencode_stays_selectable(
    tmp_path: Path,
) -> None:
    """``opencode/opencode/<model>`` must reach the CLI as ``opencode/<model>``.

    The alias prefix is stripped exactly once, at alias resolution. Anything
    downstream that strips it again destroys the provider half of the id.
    """
    argv = _argv_for_alias("opencode/opencode/big-pickle", tmp_path)

    assert _model_value(argv) == "opencode/big-pickle"


def test_opencode_regression_third_party_provider_alias_keeps_both_halves(
    tmp_path: Path,
) -> None:
    """A non-``opencode`` provider alias is unaffected by the single strip."""
    argv = _argv_for_alias("opencode/anthropic/claude-sonnet-4-5", tmp_path)

    assert _model_value(argv) == "anthropic/claude-sonnet-4-5"


def test_opencode_regression_model_flag_cannot_smuggle_extra_argv_tokens(
    tmp_path: Path,
) -> None:
    """Whitespace inside an alias must stay inside the single ``-m`` value.

    ``opencode/minimax/MiniMax-M3 --agent plan`` previously emitted
    ``['-m', 'minimax/MiniMax-M3', '--agent', 'plan']``.
    """
    argv = _argv_for_alias("opencode/minimax/MiniMax-M3 --agent plan", tmp_path)

    assert _model_value(argv) == "minimax/MiniMax-M3 --agent plan"
    assert "--agent" not in argv
    assert "plan" not in argv


def test_opencode_regression_preflight_model_id_keeps_provider_prefix() -> None:
    """The preflight must see ``provider/model``, including provider ``opencode``.

    ``validate_local_model_support`` returns ``None`` (no preflight) when the
    id carries no ``/``; an extra strip therefore blinded the preflight for
    every model under the ``opencode`` provider.
    """
    assert opencode_model_id_from_flag("-m opencode/big-pickle") == "opencode/big-pickle"
    assert opencode_model_id_from_flag("--model anthropic/claude-sonnet-4-5") == (
        "anthropic/claude-sonnet-4-5"
    )
    assert opencode_model_id_from_flag("opencode/big-pickle") == "opencode/big-pickle"
    assert opencode_model_id_from_flag(None) is None
    assert opencode_model_id_from_flag("") is None


def test_opencode_regression_model_flag_rejects_a_second_flag_as_its_value(
    tmp_path: Path,
) -> None:
    """A caller-supplied flag must not be able to pose as the model value."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("do the thing", encoding="utf-8")
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)

    with pytest.raises(ValueError, match="flag-injection"):
        build_command(
            config,
            str(prompt_file),
            options=BuildCommandOptions(
                model_flag="-m --agent",
                workspace_path=tmp_path,
            ),
        )
