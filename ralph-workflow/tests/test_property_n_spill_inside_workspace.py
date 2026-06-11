# property-test: N — spill paths inside the workspace root, agent can read what it is told to read
"""The agent can always read what it is told to read.

Any path, file, or artifact the system tells the agent to read must be
reachable through the agent's own workspace-scoped read tools. The
default spill location for oversized exec output is
``<workspace>/.agent/tmp`` — inside the workspace, reachable by the
agent's read tools.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.tools import _exec_output_spill as spill_mod
from ralph.mcp.tools._exec_output_spill import format_or_spill
from ralph.mcp.tools.exec import ExecRunDeps, resolve_spill_dir

if TYPE_CHECKING:
    import pytest


class _FakeWorkspace:
    """Minimal workspace stub returning a known root for resolve_spill_dir.

    _workspace_root looks for the .root attribute; this stub provides it.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def workspace_root(self) -> Path:
        return self.root


def test_resolve_spill_dir_defaults_to_workspace_agent_tmp(tmp_path: Path) -> None:
    """resolve_spill_dir(workspace, deps=None) returns <workspace>/.agent/tmp."""
    workspace = _FakeWorkspace(tmp_path)
    spill = resolve_spill_dir(workspace, None)
    assert spill == tmp_path / ".agent" / "tmp"


def test_format_or_spill_writes_under_workspace_agent_tmp(tmp_path: Path) -> None:
    """format_or_spill writes the spill file under <workspace>/.agent/tmp."""
    spill_dir = tmp_path / ".agent" / "tmp"
    spill_dir.mkdir(parents=True, exist_ok=True)
    # A large text triggers a spill
    large_text = "x" * (1024 * 1024 * 2)  # 2 MB
    result = format_or_spill(
        large_text,
        returncode=0,
        truncated=False,
        spill_dir=spill_dir,
    )
    # The result mentions a path
    assert result.content
    text = result.content[0].text
    assert "Read it with the read tools" in text
    # The spill file exists under spill_dir
    files = list(spill_dir.iterdir())
    assert files, f"no spill file created in {spill_dir}"
    spill_file = files[0]
    assert spill_file.is_file()
    # The spill file is INSIDE the workspace root
    assert spill_file.resolve().is_relative_to(tmp_path.resolve()), (
        f"spill file {spill_file} must be inside workspace {tmp_path}"
    )


def test_spill_file_is_readable_by_workspace_scoped_read(tmp_path: Path) -> None:
    """The spill file is readable through the agent's workspace-scoped read tools.

    The test reads the file directly (the workspace read tool would
    accept it because the path resolves inside the workspace root). The
    point: a workspace-scoped read can reach the path.
    """
    spill_dir = tmp_path / ".agent" / "tmp"
    spill_dir.mkdir(parents=True, exist_ok=True)
    large_text = "this is some very long output that should trigger a spill\n" * 100_000
    format_or_spill(
        large_text,
        returncode=0,
        truncated=False,
        spill_dir=spill_dir,
    )
    files = list(spill_dir.iterdir())
    assert files
    spill_file = files[0]
    # Read the file — the agent's read tool would also succeed because
    # the path is inside the workspace root.
    content = spill_file.read_text(encoding="utf-8")
    assert "very long output" in content


def test_resolve_spill_dir_with_injected_deps_uses_deps_value(tmp_path: Path) -> None:
    """When deps.spill_dir is set, resolve_spill_dir returns it (override seam)."""
    workspace = _FakeWorkspace(tmp_path)
    custom_spill = Path("/tmp/custom_spill_dir")
    deps = ExecRunDeps(spill_dir=custom_spill)
    spill = resolve_spill_dir(workspace, deps)
    assert spill == custom_spill


def test_resolve_spill_dir_default_inside_workspace_root(tmp_path: Path) -> None:
    """The default spill path is inside the workspace root — never outside."""
    workspace = _FakeWorkspace(tmp_path)
    spill = resolve_spill_dir(workspace, None)
    # Verify the resolved spill is inside the workspace
    assert spill.resolve().is_relative_to(tmp_path.resolve()), (
        f"default spill {spill} must be inside workspace {tmp_path}"
    )


def test_resolve_spill_dir_does_not_use_os_temp_dir() -> None:
    """resolve_spill_dir NEVER defaults to /tmp or tempfile.gettempdir().

    The bug PROMPT.md describes: oversized exec output was spilled to
    the OS temp dir, but the agent's read tools reject any path outside
    the workspace, so the agent went blind on large outputs. The
    default MUST be inside the workspace.
    """
    workspace = _FakeWorkspace(Path("/workspace/root/that/does/not/exist"))
    spill = resolve_spill_dir(workspace, None)
    temp_dir = Path(tempfile.gettempdir()).resolve()
    # The default is NOT the OS temp dir
    # The spill must NOT be under the OS temp dir; it is under /workspace
    spill_resolved = spill.resolve()
    is_in_temp = False
    try:
        is_in_temp = spill_resolved.is_relative_to(temp_dir)
    except ValueError:
        is_in_temp = False
    assert not is_in_temp, f"spill {spill} must not be inside OS temp dir {temp_dir}"


def test_format_or_spill_does_not_truncate_small_text(tmp_path: Path) -> None:
    """format_or_spill returns the text inline when it is small enough."""
    spill_dir = tmp_path / ".agent" / "tmp"
    spill_dir.mkdir(parents=True, exist_ok=True)
    small_text = "small output"
    result = format_or_spill(
        small_text,
        returncode=0,
        truncated=False,
        spill_dir=spill_dir,
    )
    assert result.content
    # The inline result preserves the text
    assert result.content[0].text == small_text
    # No spill file is created
    assert not list(spill_dir.iterdir())


def test_format_or_spill_forced_truncated_writes_file(tmp_path: Path) -> None:
    """A truncated result (truncated=True) always spills, even if small."""
    spill_dir = tmp_path / ".agent" / "tmp"
    spill_dir.mkdir(parents=True, exist_ok=True)
    format_or_spill(
        "small but truncated",
        returncode=0,
        truncated=True,
        spill_dir=spill_dir,
    )
    files = list(spill_dir.iterdir())
    assert files, "truncated=True must spill even small text"
    # The spill is inside the workspace
    assert files[0].resolve().is_relative_to(tmp_path.resolve())


def test_format_or_spill_prunes_old_spill_files_when_budget_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated oversized exec output must not grow the spill cache without bound."""
    spill_dir = tmp_path / ".agent" / "tmp"
    spill_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(spill_mod, "SPILL_CACHE_MAX_TOTAL_BYTES", 150)

    for payload in ("a" * 80, "b" * 80, "c" * 80):
        spill_mod.format_or_spill(
            payload,
            returncode=0,
            truncated=True,
            spill_dir=spill_dir,
        )

    spill_files = sorted(spill_dir.glob("ralph-exec-*.txt"))
    assert len(spill_files) == 1
    assert spill_files[0].read_text(encoding="utf-8") == "c" * 80
    assert sum(path.stat().st_size for path in spill_files) <= 150


def test_spill_file_path_under_workspace_for_explicit_workspace(tmp_path: Path) -> None:
    """An explicit workspace root causes the spill to land under that root."""
    workspace = _FakeWorkspace(tmp_path)
    spill = resolve_spill_dir(workspace, None)
    # The relative path is .agent/tmp
    assert spill.relative_to(tmp_path) == Path(".agent") / "tmp"
