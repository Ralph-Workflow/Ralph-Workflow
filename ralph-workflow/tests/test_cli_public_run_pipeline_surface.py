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


_REPLACEMENT_EXIT_CODE = 0

_BYPASSED_EXIT_CODE = 42


def _display_context() -> DisplayContext:
    return make_display_context(console=Console(file=io.StringIO()), env={})


def _dry_run_opts() -> ralph_cli_main.RunPipelineOpts:
    return ralph_cli_main.RunPipelineOpts(
        cli_overrides={},
        dry_run=True,
        resume=False,
        no_resume=False,
    )


def _bypassed_run_pipeline(*_args: object, **_kwargs: object) -> int:
    return _BYPASSED_EXIT_CODE


def test_public_run_pipeline_is_exposed_and_callable() -> None:
    run_pipeline = ralph_cli_main.run_pipeline
    assert callable(run_pipeline), "ralph.cli.main.run_pipeline must be callable"


def test_cli_dispatch_calls_the_module_level_run_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
