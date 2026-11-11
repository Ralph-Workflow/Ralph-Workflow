from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_route_page_links.py"


_LINKCHECK_TIMEOUT_SECONDS = 5


def _run_linkcheck(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    assert _SCRIPT_PATH.is_file(), (
        f"check_route_page_links.py not found at {_SCRIPT_PATH!r}; "
        "test setup is broken (script must live at scripts/check_route_page_links.py)"
    )
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), *args],
        cwd=str(cwd) if cwd is not None else str(_REPO_ROOT),
        timeout=_LINKCHECK_TIMEOUT_SECONDS,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def _write_passing_link_fixtures(tmp_path: Path) -> tuple[str, ...]:
    (tmp_path / "README.md").write_text(
        textwrap.dedent(
            """\
            # Test page
            See [the first run](README.md#first-run).
            """
        ),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    code_style = docs / "code-style"
    code_style.mkdir()
    (code_style / "index.md").write_text("# code-style\n", encoding="utf-8")
    (docs / "README.md").write_text(
        textwrap.dedent(
            """\
            # docs index
            See [code-style](code-style/index.md).
            """
        ),
        encoding="utf-8",
    )
    fragment_dir = tmp_path / "fragment"
    fragment_dir.mkdir()
    (fragment_dir / "START_HERE.md").write_text(
        textwrap.dedent(
            """\
            # Start
            See [later section](#later).
            """
        ),
        encoding="utf-8",
    )
    return ("README.md", "docs/README.md", "fragment/START_HERE.md")


@pytest.mark.timeout_seconds(10)
def test_passing_internal_links_batch(tmp_path: Path) -> None:
    paths = _write_passing_link_fixtures(tmp_path)
    result = _run_linkcheck(*paths, cwd=tmp_path)
    assert result.returncode == 0, (
        f"batched internal links must pass; "
        f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout, f"batched pass path must emit OK marker; stdout={result.stdout!r}"
    assert str(len(paths)) in result.stdout, (
        f"OK line must report file count; stdout={result.stdout!r}"
    )


@pytest.mark.timeout_seconds(10)
def test_missing_target_file_fails_with_per_line_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "START_HERE.md").write_text(
        textwrap.dedent(
            """\
            # Start
            See [missing](nope/missing.md).
            """
        ),
        encoding="utf-8",
    )
    result = _run_linkcheck("START_HERE.md", cwd=tmp_path)
    assert result.returncode != 0, (
        f"broken internal link must fail the gate; "
        f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "START_HERE.md" in combined, (
        f"failure output must name the source file; got {combined!r}"
    )
    assert "nope/missing.md" in combined, (
        f"failure output must name the offending URL; got {combined!r}"
    )


@pytest.mark.timeout_seconds(10)
def test_missing_source_file_reports_missing_route_file(tmp_path: Path) -> None:
    result = _run_linkcheck("does-not-exist.md", cwd=tmp_path)
    assert result.returncode != 0, (
        f"missing source file must fail the gate; "
        f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "missing route file" in combined, (
        f"missing source must report 'missing route file'; got {combined!r}"
    )
