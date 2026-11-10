"""Contract tests for the public ``ralph.cli.main.run_pipeline`` surface.

``ralph.cli.main.run_pipeline`` is the PUBLIC run entry point of the CLI.
Other layers reach the pipeline through it, and the CLI's own dispatch
(``ralph.cli.main.invoke_pipeline``, the public name the CLI entry point
calls) must go through that very module attribute -- so a caller who
replaces the attribute replaces the call the CLI makes.

This file pins that contract and nothing else. The pipeline's own behaviour
is covered by the CLI suites; what is pinned here is the SHAPE of the public
surface and the fact that the CLI dispatch is routed through it:

* the attribute exists on the module and is callable;
* replacing the module attribute intercepts the call the CLI dispatch makes,
  and the replacement's exit code is the exit code the dispatch returns.

Every assertion is made on the OBSERVABLE effect (was the replacement
invoked, and which exit code came back) and never on HOW the dispatch
reaches the attribute, so any implementation that keeps the public
contract is accepted.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

import ralph.cli.commands.run as ralph_cli_run
import ralph.cli.main as ralph_cli_main
from ralph.display.context import make_display_context

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.context import DisplayContext


#: Exit code the patched module attribute returns. The CLI dispatch must
#: hand this value back unchanged: that is the observable proof that the
#: patched callable is the one the dispatch invoked.
_REPLACEMENT_EXIT_CODE = 0

#: Exit code returned by the safety net below. It is distinct from
#: ``_REPLACEMENT_EXIT_CODE`` so a dispatch that reaches the
#: implementation module instead of the module attribute is reported as
#: such instead of masquerading as a success.
_BYPASSED_EXIT_CODE = 42


def _display_context() -> DisplayContext:
    """Return a real DisplayContext whose console writes into a throwaway buffer."""
    return make_display_context(console=Console(file=io.StringIO()), env={})


def _dry_run_opts() -> ralph_cli_main.RunPipelineOpts:
    """Return the dry-run options the CLI dispatch accepts."""
    return ralph_cli_main.RunPipelineOpts(
        cli_overrides={},
        dry_run=True,
        resume=False,
        no_resume=False,
    )


def _bypassed_run_pipeline(*_args: object, **_kwargs: object) -> int:
    """Stand in for the implementation module's own entry point.

    This is a SAFETY NET, not an assertion: it makes sure a dispatch that
    bypasses the module attribute cannot start a real run, and it reports
    the bypass through a distinctive exit code.
    """
    return _BYPASSED_EXIT_CODE


def test_public_run_pipeline_is_exposed_and_callable() -> None:
    """The PUBLIC run entry point MUST exist on the module and be callable.

    Every layer that reaches the pipeline reaches it through this module
    attribute, so losing it is a public-surface regression even when the
    CLI keeps working through some other path.
    """
    run_pipeline = ralph_cli_main.run_pipeline
    assert callable(run_pipeline), "ralph.cli.main.run_pipeline must be callable"


def test_cli_dispatch_calls_the_module_level_run_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The CLI dispatch MUST go through the module-level ``run_pipeline``.

    Replacing the module attribute has to intercept the call the dispatch
    makes; otherwise the replacement only affects direct callers of the
    attribute and the CLI silently keeps running its own path.

    The dispatch is reached through its PUBLIC name (``invoke_pipeline``),
    the name the CLI entry point itself calls, so an operator's replacement
    of the public attribute is the path that has to be intercepted.
    """
    # RALPH-TEST-REASON: the dispatch was reached through the private
    # ``_run_pipeline`` name; the public alias ``invoke_pipeline`` is the
    # same dispatch, so the two dispatch tests were duplicates of each
    # other. They are merged into ONE test that keeps BOTH observable
    # assertions: the replacement was invoked, and the dispatch returned the
    # replacement's exit code.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")
    monkeypatch.setattr(ralph_cli_run, "run_pipeline", _bypassed_run_pipeline, raising=False)

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _replacement(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return _REPLACEMENT_EXIT_CODE

    monkeypatch.setattr(ralph_cli_main, "run_pipeline", _replacement)

    exit_code = ralph_cli_main.invoke_pipeline(
        config=None,
        opts=_dry_run_opts(),
        display_context=_display_context(),
    )

    assert calls, (
        "ralph.cli.main.invoke_pipeline must dispatch through the module-level "
        f"run_pipeline attribute; the replacement was never invoked "
        f"(exit code was {exit_code})"
    )
    assert exit_code == _REPLACEMENT_EXIT_CODE, (
        "the CLI dispatch must return the exit code of the callable it invoked; "
        f"expected {_REPLACEMENT_EXIT_CODE}, got {exit_code}"
    )
