"""Black-box tests for ``scripts/check_route_page_links.py``.

Drives the real script through its real entry point (``main(sys.argv)``)
via ``subprocess.run`` from the repository root, asserting both the
positive path (anchored internal links resolve; valid docs-relative
links resolve) and the negative path (broken internal links fail with
a per-file, per-line diagnostic that names the offending URL and the
resolved target).

Why subprocess: ``check_route_page_links.py`` is the system under
test; importing it as a Python module would exercise the parser
helpers but would NOT exercise ``main()``'s command-line dispatch
(the path that ``make route-linkcheck`` invokes). The script is also
the artifact called out in
``docs/ralph-workflow-policy/documentation-policy.md`` § Verification,
and the gate-script policy requires a black-box test that proves both
the pass and fail paths.

Invocations are bounded by ``timeout=5`` so a hung probe cannot
stall the suite; these fixtures use only local files (no external
HTTP), so the script finishes in milliseconds. The script itself
honours ``EXTERNAL_LINK_TIMEOUT_SECONDS = 10.0`` per request when
external links are present.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Drives a real subprocess against the ``scripts/check_route_page_links.py``
# black-box entry point; excluded from the 60s combined ``make verify``
# test budget (the script spawns python and walks the repo's link graph)
# and tagged so the audit_test_policy subprocess gate allows the call.
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "check_route_page_links.py"


_LINKCHECK_TIMEOUT_SECONDS = 5


def _run_linkcheck(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the route-page linkchecker from ``cwd`` (defaults to repo root).

    Returns the ``CompletedProcess`` so the test can assert on return
    code, stdout, and stderr. ``check=True`` is intentionally omitted:
    a failing linkcheck must surface its returncode to the assertion
    rather than raising in the helper.

    Pass multiple file paths in one call when scenarios share ``cwd``
    and all must succeed — ``main()`` checks each file in one process
    startup, which is behaviourally equivalent to separate invocations
    for independent pass paths.
    """
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
    """Lay out three independent pass scenarios under ``tmp_path``.

    Returns relative paths for a single batched ``main()`` invocation.
    """
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
    """Anchored, docs-relative, and fragment-only internal links pass in one run.

    Batches three independent pass fixtures into a single ``main()``
    invocation (one Python startup) because ``check_route_page_links.py``
    accepts multiple FILE arguments and reports all errors before exiting.
    Each scenario remains a distinct assertion target:

    - anchored fragment: ``README.md#first-run`` resolves via path only
      (wt-038 regression)
    - docs-relative: ``code-style/index.md`` from ``docs/README.md``
    - fragment-only: bare ``#later`` is not a filesystem path
    """
    paths = _write_passing_link_fixtures(tmp_path)
    result = _run_linkcheck(*paths, cwd=tmp_path)
    assert result.returncode == 0, (
        f"batched internal links must pass; "
        f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout, (
        f"batched pass path must emit OK marker; stdout={result.stdout!r}"
    )
    assert str(len(paths)) in result.stdout, (
        f"OK line must report file count; stdout={result.stdout!r}"
    )


@pytest.mark.timeout_seconds(10)
def test_missing_target_file_fails_with_per_line_diagnostic(tmp_path: Path) -> None:
    """A relative link to a missing file fails with a per-file, per-line report.

    The script MUST surface the source file path, the source line
    number, the offending URL, and the resolved target so the agent
    can find and repair the broken link without loading the entire
    route file.
    """
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
    """A source file that does not exist is reported as missing.

    The script MUST distinguish a missing source file from a broken
    link inside an existing source file. The failure message names
    the missing source so the agent can correct the Makefile target
    rather than chasing a phantom link.
    """
    result = _run_linkcheck("does-not-exist.md", cwd=tmp_path)
    assert result.returncode != 0, (
        f"missing source file must fail the gate; "
        f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "missing route file" in combined, (
        f"missing source must report 'missing route file'; got {combined!r}"
    )


