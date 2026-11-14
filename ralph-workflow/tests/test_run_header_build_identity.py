from __future__ import annotations

import re
from io import StringIO

from rich.console import Console

from ralph import version
from ralph.display import RunStartOrientation, make_display_context
from ralph.display.parallel_display import ParallelDisplay

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "flaky timestamp": "strip wall-clock timestamp prefix so stability test does not flake across second boundaries",
}

_BUILD_IDENTITY_PATTERN = re.compile(r"v(?P<version>[0-9][0-9A-Za-z.+-]*)\+(?P<commit>[0-9a-f]{8})")


def _render_run_start() -> str:
    buffer = StringIO()
    console = Console(
        file=buffer,
        force_terminal=False,
        highlight=False,
        color_system=None,
        width=200,
    )
    display = ParallelDisplay(make_display_context(console=console, env={}))
    display.emit_run_start(RunStartOrientation(plan_present=False))
    return buffer.getvalue()


def _run_start_header(output: str) -> str:
    headers = [line for line in output.splitlines() if "Ralph Workflow run start" in line]
    assert len(headers) == 1, f"expected exactly one run-start header line, got: {headers!r}"
    return re.sub(r"^\d{2}:\d{2}:\d{2}\s+", "", headers[0])


def test_run_start_header_carries_package_version() -> None:
    header = _run_start_header(_render_run_start())
    assert f"v{version}+" in header, f"run-start header carries no package version: {header!r}"


def test_run_start_header_carries_commit_sha() -> None:
    header = _run_start_header(_render_run_start())
    match = _BUILD_IDENTITY_PATTERN.search(header)
    assert match is not None, f"run-start header carries no commit SHA: {header!r}"
    assert match.group("version") == version


def test_run_start_header_carries_the_build_identity_once() -> None:
    header = _run_start_header(_render_run_start())
    assert header.count(f"v{version}+") == 1, (
        f"run-start header must carry the build identity exactly once: {header!r}"
    )


def test_run_start_header_build_identity_is_stable_across_renders() -> None:
    first = _run_start_header(_render_run_start())
    second = _run_start_header(_render_run_start())
    assert first == second
