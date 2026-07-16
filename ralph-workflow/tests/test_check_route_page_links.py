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

Each invocation is bounded by ``timeout=15`` so a hung probe cannot
stall the suite; the script itself honours
``EXTERNAL_LINK_TIMEOUT_SECONDS = 10.0`` per request.
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


def _run_linkcheck(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the route-page linkchecker from ``cwd`` (defaults to repo root).

    Returns the ``CompletedProcess`` so the test can assert on return
    code, stdout, and stderr. ``check=True`` is intentionally omitted:
    a failing linkcheck must surface its returncode to the assertion
    rather than raising in the helper.
    """
    assert _SCRIPT_PATH.is_file(), (
        f"check_route_page_links.py not found at {_SCRIPT_PATH!r}; "
        "test setup is broken (script must live at scripts/check_route_page_links.py)"
    )
    return subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), *args],
        cwd=str(cwd) if cwd is not None else str(_REPO_ROOT),
        timeout=15,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


@pytest.mark.timeout_seconds(20)
def test_anchored_internal_link_passes_when_target_file_exists(tmp_path: Path) -> None:
    """A relative link carrying a fragment resolves via the path part only.

    The script MUST strip the ``#anchor`` fragment before path
    resolution. ``README.md#first-run`` is a link to ``README.md``
    with a Markdown anchor; the script must NOT treat ``README.md#first-run``
    as a literal filename. This was the regression fixed at
    wt-038: the pre-fix script reported every anchored internal
    link as broken because ``Path.exists()`` rejected the fragment.
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
    result = _run_linkcheck("README.md", cwd=tmp_path)
    assert result.returncode == 0, (
        f"anchored internal link must pass; "
        f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout, (
        f"anchored internal link must emit OK marker; "
        f"stdout={result.stdout!r}"
    )


@pytest.mark.timeout_seconds(20)
def test_docs_relative_internal_link_passes_when_resolved_from_source_dir(
    tmp_path: Path,
) -> None:
    """A docs-relative link resolves from the source document's directory.

    The script MUST resolve ``../code-style/index.md`` from the
    source document's parent directory (``docs/``), not from the
    repository root. This was the regression reported at wt-038: the
    pre-fix script was suspected of resolving from the repository
    root. The post-fix script resolves relative to the source
    document, so a valid ``docs/code-style/index.md`` link from
    ``docs/README.md`` passes.
    """
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
    result = _run_linkcheck("docs/README.md", cwd=tmp_path)
    assert result.returncode == 0, (
        f"docs-relative link must pass; "
        f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )


@pytest.mark.timeout_seconds(20)
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


@pytest.mark.timeout_seconds(20)
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


@pytest.mark.timeout_seconds(20)
def test_fragment_only_link_is_not_a_broken_link(tmp_path: Path) -> None:
    """A bare ``#anchor`` link is treated as an in-page fragment, not a path.

    The script MUST NOT attempt to resolve a fragment-only link to a
    filesystem path. ``#section`` is a valid Markdown anchor that
    points into the same page; the script must ignore it rather
    than flag it as broken.
    """
    (tmp_path / "START_HERE.md").write_text(
        textwrap.dedent(
            """\
            # Start
            See [later section](#later).
            """
        ),
        encoding="utf-8",
    )
    result = _run_linkcheck("START_HERE.md", cwd=tmp_path)
    assert result.returncode == 0, (
        f"fragment-only link must pass; "
        f"rc={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
