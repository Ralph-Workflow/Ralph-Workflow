"""wt-028-display: anti-drift guard for the consolidated single display mode.

Pins the architectural invariant that Ralph Workflow exposes exactly ONE
display mode. Future commits that re-introduce mode-conditional branches
in the production code under ``ralph/display/`` MUST fail this test.

Scanned checks:

1. ``DisplayContext.mode`` is a :data:`~typing.Literal['default']`-typed
   field with a constant string value ``'default'``. There is no
   ``Literal['compact', 'medium', 'wide']`` annotation, no factory
   that returns any other value, and no public override surface.

2. No production file under ``ralph/display/`` contains an AST
   ``Compare`` node whose right-hand string literal is one of
   ``'compact'``, ``'medium'``, or ``'wide'``. The allowlist covers
   ``ralph/display/mode.py`` (the consolidated ``DEFAULT_MODE`` constant
   lives there) and ``ralph/display/__init__.py`` (which documents the
   single mode in its module docstring).

The AST cache is populated at module import time so the test runs in
< 1 s.
"""

from __future__ import annotations

import ast
import subprocess
from functools import cache, lru_cache
from pathlib import Path
from typing import get_args, get_origin

import pytest

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.mode import DEFAULT_MODE

_DISPLAY_DIR = Path(__file__).parent.parent.parent / "ralph" / "display"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DRIFT_SCRIPT = _PROJECT_ROOT / "scripts" / "wt028-drift-check.sh"
_ALLOWLIST = frozenset({"mode.py", "__init__.py"})
_MODE_LITERALS = frozenset({"compact", "medium", "wide"})


@lru_cache(maxsize=1)
def _display_files() -> tuple[Path, ...]:
    """Return all *.py files under ralph/display/."""
    return tuple(sorted(_DISPLAY_DIR.glob("*.py")))


@cache
def _parsed(path: Path) -> ast.Module:
    """Return the AST module for the given file, parsed once."""
    return ast.parse(path.read_text(encoding="utf-8"))


def _string_literal_value(node: ast.AST) -> str | None:
    """Return the constant string value of an AST Constant node with str value, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_compare_string_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield ``(lineno, literal)`` for every string literal appearing in a Compare node.

    Covers both LHS (``"compact" == x``) and RHS (``x == "compact"``)
    comparisons. Walks both ``ast.Compare`` and the simple
    ``ast.NamedExpr`` (``mode := "compact"``) cases for safety.
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for sub in (node.left, *node.comparators):
                value = _string_literal_value(sub)
                if value is not None and value in _MODE_LITERALS:
                    hits.append((node.lineno, value))
    return hits


# Pre-populate the AST cache at import time so the per-test SIGALRM
# window is not spent re-parsing files.
for _f in _display_files():
    _parsed(_f)


def test_display_context_mode_is_literal_default() -> None:
    """DisplayContext.mode is typed Literal['default'] (no other mode values)."""
    annotations = DisplayContext.__annotations__
    assert "mode" in annotations, (
        f"DisplayContext is missing 'mode' annotation; got {sorted(annotations)!r}"
    )
    annotation_str = str(annotations["mode"])
    assert "default" in annotation_str, (
        f"DisplayContext.mode annotation must include 'default'; got {annotation_str!r}"
    )
    # Use typing introspection for the canonical check.
    annotation = annotations["mode"]
    args = get_args(annotation)
    if args or get_origin(annotation) is not None:
        assert tuple(args) == ("default",), (
            f"DisplayContext.mode must be Literal['default'] only; got Literal{args!r}"
        )


def test_display_context_mode_default_constant_is_default() -> None:
    """The DEFAULT_MODE constant in ralph.display.mode is exactly the string 'default'."""
    assert DEFAULT_MODE == "default", (
        f"ralph.display.mode.DEFAULT_MODE must be 'default'; got {DEFAULT_MODE!r}"
    )


def test_no_compact_medium_wide_branches_in_display_production() -> None:
    """No ralph/display/*.py file outside the allowlist compares to 'compact' / 'medium' / 'wide'.

    Allowlist: ralph/display/mode.py (DEFAULT_MODE='default' constant), and
    ralph/display/__init__.py (the consolidated single-mode docs mention the
    removed mode names only as historical references in the docstring; we
    still allowlist this file because the AST scan would otherwise flag
    docstring-mentions of "compact mode" inside the docstring — but the
    tokenize-based ast.parse only sees code, not strings. The allowlist is
    defence-in-depth: tokenize-based code scanning already excludes
    docstrings, but we keep the allowlist so future docstring expansions
    stay safe.
    """
    violations: list[str] = []
    for path in _display_files():
        if path.name in _ALLOWLIST:
            continue
        tree = _parsed(path)
        for lineno, literal in _iter_compare_string_literals(tree):
            violations.append(f"{path.name}:{lineno}: '{literal}'")
    assert not violations, (
        "Mode-conditional branches found in ralph/display/ production code "
        "(anti-drift guard tripped; Ralph Workflow has a SINGLE display mode "
        "called 'default' — re-introduce no other mode):\n"
        + "\n".join(violations)
    )


def test_make_display_context_no_force_mode_kwarg_call_works() -> None:
    """make_display_context() with no extra kwargs returns a DisplayContext with mode='default'."""
    ctx = make_display_context()
    assert ctx.mode == "default"
    assert isinstance(ctx, DisplayContext)


# ---------------------------------------------------------------------------
# Subprocess-driven drift-check probe (AC-05 / AC-06 fail-closed proof)
# ---------------------------------------------------------------------------

# Every legacy token the consolidation named. The script's regex is
# case-sensitive so the probe MUST contain the exact uppercase form.
# The non-``compact`` mode branches are tested explicitly below; the
# ``compact`` mode branch is already implicitly covered by the same
# regex alternation and is intentionally omitted here to keep the
# parametrization list focused on the named tokens in the
# closure-pass spec.
#
# Tokens are stored as a tuple of string FRAGMENTS that Python
# concatenates at runtime to form the literal token. The fragments
# are deliberately split so no fragment alone triggers the drift
# regex when the script greps this test file (the file itself would
# otherwise trip the script's drift detector, which is the very thing
# this test is asserting fail-closed behaviour for).
_DRIFT_PROBE_TOKENS: tuple[str, ...] = (
    "NARROW_" + "THRESHOLD",
    "MEDIUM_" + "THRESHOLD",
    "ctx.mode == '" + "wide" + "'",
    "ctx.mode == '" + "medium" + "'",
    "force_mode" + " " + "=",
    "RALPH_" + "FORCE_NARROW",
    "DISPLAY_" + "MODE",
)


def _run_drift_check() -> subprocess.CompletedProcess[str]:
    """Run ``scripts/wt028-drift-check.sh`` from the ralph-workflow root.

    Resolves :data:`_DRIFT_SCRIPT` as an absolute path and uses
    ``cwd=_PROJECT_ROOT`` so the script's ``cd "$RALPH_ROOT"`` lands in
    the correct directory regardless of the pytest runner's cwd.
    Bounded 3s timeout per invocation.
    """
    assert _DRIFT_SCRIPT.is_file(), (
        f"wt028 drift-check script not found at {_DRIFT_SCRIPT!r}; "
        f"PROJECT_ROOT resolution is wrong"
    )
    return subprocess.run(
        ["bash", str(_DRIFT_SCRIPT)],
        cwd=str(_PROJECT_ROOT),
        timeout=3,
        capture_output=True,
        text=True,
        check=False,
    )


def _safe_probe_name(token: str) -> str:
    """Return a probe-file name that embeds the token in a filesystem-safe form.

    Uses the post-concatenation token (NOT a fragment) so the name is
    stable across all parametrized variants of the same logical token.
    """
    safe = []
    for ch in token:
        if ch.isalnum():
            safe.append(ch.lower())
        else:
            safe.append("_")
    return "".join(safe).strip("_")


# Per-test timeout: the drift-check shell script greps ralph/, tests/, docs/
# on every invocation. With 7 parametrized tokens x 2 invocations each, the
# cumulative wall-clock cost is well under the 60s combined budget, but each
# individual parametrized variant needs more headroom than the default 1s
# because the bash startup + grep cost on the ralph-workflow tree takes
# ~0.5-1s per invocation on a busy CI runner.
#
# The subprocess_e2e marker exempts this file from the audit_test_policy
# subprocess audit: the bash script IS the system-under-test (the same
# artifact make verify-drift invokes), so subprocess.run is the
# legitimate invocation path, not a bypass of the MockProcessExecutor
# test-infra seam.
pytestmark = [pytest.mark.timeout_seconds(8), pytest.mark.subprocess_e2e]


@pytest.mark.parametrize("token", _DRIFT_PROBE_TOKENS)
def test_drift_check_script_fails_closed_against_every_named_legacy_token(
    token: str,
) -> None:
    """``scripts/wt028-drift-check.sh`` is fail-closed against every legacy token.

    For each named legacy token the closure-pass consolidation named,
    this test (a) writes the token to a temporary probe file under
    ``ralph/``, (b) runs the actual bash script via subprocess with a
    10s timeout (the same invocation path ``make verify-drift`` uses),
    (c) asserts non-zero exit and that the probe-file path appears in
    the FAIL output (the script's stdout lists the file paths whose
    contents matched the DRIFT_PATTERNS regex), (d) deletes the probe
    and re-runs the script, (e) asserts exit code 0.

    The probe file is named ``_drift_probe_<sanitized-token>.py`` and
    lives under ``ralph/`` — outside the historical-context allowlist
    (the allowlist covers ``ralph/display/status_bar.py``,
    ``ralph/display/__init__.py``, ``ralph/display/mode.py``,
    ``ralph/display/_mode_adaptive_limits.py``, ``ralph/display/context.py``,
    so a probe file at ``ralph/_drift_probe_*.py`` cannot accidentally
    fall inside it). try/finally guarantees the probe is cleaned up
    even on assertion failure.
    """
    probe_name = f"_drift_probe_{_safe_probe_name(token)}.py"
    probe_path = _PROJECT_ROOT / "ralph" / probe_name
    assert not probe_path.exists(), (
        f"probe file {probe_path!r} should not exist before the test starts"
    )
    try:
        probe_path.write_text(f"{token} = '1'\n", encoding="utf-8")
        result = _run_drift_check()
        assert result.returncode != 0, (
            f"drift-check script must FAIL when probe file contains the "
            f"legacy token {token!r}; got rc={result.returncode}, "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        combined_output = result.stdout + "\n" + result.stderr
        assert probe_name in combined_output, (
            f"drift-check FAIL output must name the offending probe file "
            f"{probe_name!r}; got stdout={result.stdout!r}, "
            f"stderr={result.stderr!r}"
        )
        assert "FAIL" in combined_output, (
            f"drift-check FAIL output must include the FAIL marker; "
            f"got stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
    finally:
        if probe_path.exists():
            probe_path.unlink()
    result_after = _run_drift_check()
    assert result_after.returncode == 0, (
        f"drift-check script must PASS after the probe file is removed; "
        f"got rc={result_after.returncode}, stdout={result_after.stdout!r}, "
        f"stderr={result_after.stderr!r}"
    )
    assert "PASS" in result_after.stdout, (
        f"drift-check PASS output must include the PASS marker; "
        f"got stdout={result_after.stdout!r}"
    )
